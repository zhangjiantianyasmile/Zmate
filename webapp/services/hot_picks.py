"""值得关注的热点：基于知乎热榜 Top 20 + Moonshot v1 8k 精选 Top 5。

数据流：
    1. 优先读 webapp/cache/hot_picks.json，命中且未过期（15 分钟）直接返回。
    2. 缓存未命中时，调用 hot_list.fetch_hot_list(size=20, cache_only=True)
       拿候选——只读知乎热榜的本地缓存（webapp/cache/hot_list.json，1h TTL），
       不会因为「今日热点」按钮就触发知乎接口刷新。
    3. 优先用 moonshot-v1-8k 让模型从 Top 20 里挑出 5 条；Kimi key 缺失或失败
       时回落 DeepSeek，再失败回落本地 mock，保证接口永远有结果可返。
    4. 把结果写回 hot_picks.json（含来源、模型标识、prompt 备份），15 分钟内
       下次进入函数直接读缓存返回。

之所以保留 prompt_messages，是方便联调时直接看到当前实际下发给模型的范式。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import deepseek_client, hot_list, kimi_client, roles
from .config_loader import load_config


logger = logging.getLogger(__name__)


PICKS_TTL_SECONDS = 15 * 60  # 15 分钟
PICKS_TARGET = 5
CANDIDATE_TOP = 20
PICK_MODEL = "moonshot-v1-8k"

WEBAPP_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = WEBAPP_DIR / "cache"
PICKS_CACHE_FILE = CACHE_DIR / "hot_picks.json"

_LOCK = threading.Lock()


# ---------------- Prompt 范式（后续接入真实 DeepSeek 时直接生效） ---------------- #

# 「挑 5 条」属于热点提取师的本职工作，复用 roles 模块定义；具体「恰好 5 条 /
# 输出 JSON」等任务约束放在下面的 user prompt 里，避免两处重复维护。
PICK_SYSTEM_PROMPT = roles.get_prompt(roles.TOPIC_PICK)

PICK_USER_PROMPT_TEMPLATE = (
    "下面是今天知乎热榜的候选列表（已按热度从高到低排序）：\n\n"
    "{candidates_json}\n\n"
    "请基于以下原则筛选恰好 5 条：\n"
    "1) 优先选公共政策、社会事件、产业 / 科技进展、对普通人有信息价值的话题；\n"
    "2) 适度避开纯娱乐八卦、明星私事、个人化吐槽；\n"
    "3) 5 条尽量覆盖不同领域，主题不要重复；\n"
    "4) 推荐理由要点出「为什么值得关注」，不要复述标题，不超过 40 字；\n"
    "5) 必须从给定候选中选择，不要凭空生成新的话题或 id。\n\n"
    "严格只输出 JSON，不要带 markdown 代码块或多余文字，结构如下：\n"
    "{{\n"
    "  \"picks\": [\n"
    "    {{\"id\": \"<候选 id>\", \"reason\": \"<不超过 40 字的推荐理由>\"}}\n"
    "  ]\n"
    "}}\n"
    "picks 数组长度必须是 5。"
)


OBSERVATION_SYSTEM_PROMPT = (
    "你是 Zmate（知乎 Mate），用一段 100~120 字的中文做今日热点的『观察』总结。"
    "要求：紧扣给定的实际话题、点出潜在关联或趋势、语言克制有态度，避免空话与套话。"
)

OBSERVATION_USER_PROMPT_TEMPLATE = (
    "下面是 Zmate 已经为用户筛出的 5 条值得关注的热点（含推荐理由）：\n\n"
    "{picks_json}\n\n"
    "请基于这 5 条，写一段 100~120 字的『今日观察』，要求：\n"
    "1) 必须围绕真实给出的话题，不要凭空编造新事件；\n"
    "2) 适当点出共同趋势、共性逻辑或潜在关联；\n"
    "3) 直接给一段自然中文，不要用 markdown，不要罗列编号。"
)


# ---------------- 缓存读写 ---------------- #

def _read_cache() -> Optional[Dict[str, Any]]:
    if not PICKS_CACHE_FILE.exists():
        return None
    try:
        with PICKS_CACHE_FILE.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hot_picks cache unreadable: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _write_cache(payload: Dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PICKS_CACHE_FILE.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        tmp.replace(PICKS_CACHE_FILE)
    except OSError as exc:
        logger.warning("hot_picks cache write failed: %s", exc)


# ---------------- Prompt 构造与结果解析 ---------------- #

def _slim_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只保留模型筛选所需的最小字段，避免无谓的 token 消耗。"""
    slim: List[Dict[str, Any]] = []
    for c in candidates:
        slim.append(
            {
                "id": c.get("id"),
                "rank": c.get("rank"),
                "title": c.get("title"),
                "excerpt": (c.get("excerpt") or "")[:80],
            }
        )
    return slim


def build_pick_messages(candidates: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    user = PICK_USER_PROMPT_TEMPLATE.format(
        candidates_json=json.dumps(_slim_candidates(candidates), ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": PICK_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _slim_picks_for_observation(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "rank": p.get("rank"),
            "title": p.get("title"),
            "reason": p.get("reason") or "",
        }
        for p in picks
    ]


def build_observation_messages(picks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    user = OBSERVATION_USER_PROMPT_TEMPLATE.format(
        picks_json=json.dumps(_slim_picks_for_observation(picks), ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": OBSERVATION_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _parse_picks(raw_text: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """解析模型回复（兼容裸 JSON 与 ```json ... ``` 包裹两种形式）。"""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "hot_picks model response not json: %s | head=%s", exc, text[:200]
        )
        return []

    raw_picks = (data or {}).get("picks") or []
    by_id = {c.get("id"): c for c in candidates}
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_picks:
        if not isinstance(raw, dict):
            continue
        cid = raw.get("id")
        if cid in seen or cid not in by_id:
            continue
        seen.add(cid)
        cand = by_id[cid]
        out.append({**cand, "reason": str(raw.get("reason") or "")[:80]})
        if len(out) >= PICKS_TARGET:
            break
    return out


# ---------------- 模型调用（Kimi / DeepSeek / Mock） ---------------- #

# 落日志时统一加在文件名前面的场景标识，方便在 webapp/logs/ 下用
# `ls hotpicks-*.log` / `grep -l '"scene": "hot_picks"'` 快速捞出今日热点
# 相关的所有调用记录，与「直答 / Kimi 对话」分开排查。
_LOG_SCENE = "hot_picks"


def _log_extra(purpose: str) -> Dict[str, Any]:
    """为本场景的 LLM 调用统一打上 scene/purpose 标签，落进日志的 REQUEST.extra。

    `purpose` 区分调用语义（`pick` = 选 5 条，`observation` = 生成观察文案），
    后续如果再加入「话题预热」「评论摘要」等子任务，沿用此处约定即可。
    """
    return {"scene": _LOG_SCENE, "purpose": purpose}


def _log_prefix(purpose: str) -> str:
    return f"{_LOG_SCENE}-{purpose}"


def _ask_kimi(messages: List[Dict[str, str]], purpose: str) -> Optional[str]:
    """调用 Moonshot v1 8k。未配置 key 或调用异常时返回 None。

    Moonshot 的 free 账号开箱可用的就是 `moonshot-v1-*` 系列，正好对应今日
    热点这种轻量结构化筛选场景；显式锁定 PICK_MODEL，避免后续有人改了
    `kimi_model` 配置后把贵的思考模型默认接进这里。`purpose` 仅用于落日志
    时区分子任务（pick / observation 等）。
    """
    cfg = load_config()
    api_key = (cfg.get("kimi_api_key") or "").strip()
    if not api_key:
        return None
    client = kimi_client.KimiClient(
        api_key=api_key,
        base_url=cfg.get("kimi_base_url") or kimi_client.DEFAULT_BASE_URL,
        model=PICK_MODEL,
    )
    try:
        return client.chat_once(
            messages,
            model=PICK_MODEL,
            temperature=0.3,
            extra=_log_extra(purpose),
            log_name_prefix=_log_prefix(purpose),
        )
    except Exception as exc:  # noqa: BLE001 - 网络/权限/解析全部兜底
        logger.warning("kimi pick call failed (purpose=%s): %s", purpose, exc)
        return None


def _ask_deepseek(messages: List[Dict[str, str]], purpose: str) -> Optional[str]:
    """调用 DeepSeek。未配置 key 或调用异常时返回 None，由调用方走 mock。

    `purpose` 同 `_ask_kimi`，用于落日志时打 scene 标签。
    """
    cfg = load_config()
    api_key = (cfg.get("deepseek_api_key") or "").strip()
    if not api_key:
        return None
    client = deepseek_client.DeepSeekClient(
        api_key=api_key,
        base_url=cfg.get("deepseek_base_url") or "https://api.deepseek.com/v1",
        model=cfg.get("deepseek_model") or "deepseek-chat",
    )
    try:
        return client.chat_once(
            messages,
            temperature=0.3,
            extra=_log_extra(purpose),
            log_name_prefix=_log_prefix(purpose),
        )
    except Exception as exc:  # noqa: BLE001 - 网络/解析层全部兜底走 mock
        logger.warning("deepseek pick call failed (purpose=%s): %s", purpose, exc)
        return None


_MOCK_REASONS = [
    "公共政策/事件，影响面广，值得普通人留意",
    "产业与科技动向，行业从业者建议跟进",
    "民生议题，关系到每个人的生活体感",
    "社会观察，可能折射更深层趋势",
    "讨论度高，多元观点有助于建立判断",
]


def _mock_pick(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """没有 DeepSeek key 时的本地兜底：简单规则挑选 + 占位推荐理由。

    规则：从前 20 条里依次挑出 5 条标题去重后的候选，理由按预设循环。
    上线 DeepSeek 后，这段会自然让位给模型结果。
    """
    if not candidates:
        return []
    picked: List[Dict[str, Any]] = []
    seen_titles = set()
    for cand in candidates:
        if len(picked) >= PICKS_TARGET:
            break
        title = (cand.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        picked.append({**cand, "reason": _MOCK_REASONS[len(picked) % len(_MOCK_REASONS)]})
    return picked


def _short_title(title: str, limit: int = 18) -> str:
    """截断长标题，给观察文案留白。"""
    title = (title or "").strip()
    if len(title) <= limit:
        return title
    return title[:limit].rstrip("，。、,. ") + "…"


def _mock_observation(picks: List[Dict[str, Any]]) -> str:
    """没有 DeepSeek key 时，用真实 picks 拼出一段确定性的『今日观察』。

    必须基于实际命中的标题，而不是另写一组与 picks 无关的话题，
    否则用户在助手里看到的内容就会和上面卡片对不上号。
    """
    if not picks:
        return "今天的热榜里暂时没有捞到值得观察的内容，先让数据缓一会儿吧。"

    headline = _short_title(picks[0].get("title", ""))
    others = "、".join(
        _short_title(p.get("title", "")) for p in picks[1:4] if p.get("title")
    )
    text = (
        f"今天最值得停下来看的，是「{headline}」。"
        f"再往下，{others}等几条话题，"
        "横跨公共政策、产业进展和民生议题，能帮你大致拼出一张今天的『信息坐标』。"
        "Zmate 的建议是：先挑一两条与你工作或生活相关的进去看，"
        "再用剩下的当背景，比一口气全读更有价值。"
    )
    return text


def _generate_observation(picks: List[Dict[str, Any]]) -> str:
    """优先用 moonshot-v1-8k，其次 DeepSeek，再回退到基于 picks 的确定性模板。

    和 picks 选拔统一走 Kimi，避免两段文案分别由不同模型生成时语气割裂。
    """
    if not picks:
        return _mock_observation(picks)
    messages = build_observation_messages(picks)
    raw = _ask_kimi(messages, purpose="observation") or _ask_deepseek(
        messages, purpose="observation"
    )
    if raw and raw.strip():
        return raw.strip()
    return _mock_observation(picks)


# ---------------- 对外入口 ---------------- #

def get_hot_picks(force_refresh: bool = False) -> Dict[str, Any]:
    """返回 5 条「值得关注的热点」+ 一段对应的「今日观察」。

    返回字段：
      - picks: 5 条命中候选的完整结构（包含 reason）
      - candidates: 本次模型可见的 Top 20 候选
      - observation: 基于真实 picks 生成的一段「今日观察」（Kimi / DeepSeek / 模板）
      - hot_source: 热榜的来源（zhihu_open_api / disk-cache / mock 等）
      - model_used: moonshot-v1-8k / deepseek / mock
      - cache: hit / refresh / miss
      - fetched_at: unix 秒
      - prompt_messages: 本次实际下发给模型的 system + user 消息（便于联调）
    """
    with _LOCK:
        now = time.time()
        cached = _read_cache()
        if cached and not force_refresh:
            ts = float(cached.get("fetched_at") or 0.0)
            picks = cached.get("picks") or []
            if picks and (now - ts) < PICKS_TTL_SECONDS:
                if not cached.get("observation"):
                    # 老缓存里没有 observation，懒补一次；不影响 PICKS_TTL_SECONDS
                    cached["observation"] = _generate_observation(picks)
                    _write_cache(cached)
                logger.info(
                    "hot_picks cache hit, age=%.1fmin, model=%s",
                    max(0.0, (now - ts) / 60.0),
                    cached.get("model_used"),
                )
                return {**cached, "cache": "hit"}

        # 热榜只读缓存（webapp/cache/hot_list.json，1h TTL），不为了挑 5 条
        # 而触发对知乎开放接口的额外刷新；hot_list 自带「无缓存时 mock 兜底」。
        hot = hot_list.fetch_hot_list(size=CANDIDATE_TOP, cache_only=True)
        candidates = (hot.get("items") or [])[:CANDIDATE_TOP]
        if not candidates:
            return {
                "picks": [],
                "candidates": [],
                "observation": _mock_observation([]),
                "hot_source": hot.get("source"),
                "model_used": None,
                "cache": "miss",
                "fetched_at": now,
                "prompt_messages": [],
            }

        messages = build_pick_messages(candidates)

        picks: List[Dict[str, Any]] = []
        model_used = "mock"

        # 优先 Kimi（moonshot-v1-8k），失败再退 DeepSeek，再退本地 mock，
        # 保证「无外部 API」的部署也能跑通。每次实际请求都会被 chat_once 内部
        # 的 LLMCallLogger 记到 webapp/logs/hot_picks-pick-<model>-<ts>.log。
        raw_kimi = _ask_kimi(messages, purpose="pick")
        if raw_kimi:
            picks = _parse_picks(raw_kimi, candidates)
            if picks:
                model_used = PICK_MODEL
            else:
                logger.warning("kimi returned but parse yielded 0 picks, try deepseek")

        if not picks:
            raw_ds = _ask_deepseek(messages, purpose="pick")
            if raw_ds:
                picks = _parse_picks(raw_ds, candidates)
                if picks:
                    model_used = "deepseek"
                else:
                    logger.warning("deepseek returned but parse yielded 0 picks, fallback mock")

        if not picks:
            picks = _mock_pick(candidates)

        observation = _generate_observation(picks)

        payload = {
            "picks": picks,
            "candidates": candidates,
            "observation": observation,
            "hot_source": hot.get("source"),
            "model_used": model_used,
            "fetched_at": now,
            "prompt_messages": messages,
        }
        _write_cache(payload)
        logger.info(
            "hot_picks refreshed: %d picks, model=%s, hot_source=%s, observation_len=%d",
            len(picks), model_used, hot.get("source"), len(observation),
        )
        return {**payload, "cache": "refresh"}
