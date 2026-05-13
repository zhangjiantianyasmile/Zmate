"""值得关注的热点：基于知乎热榜 Top 20 + DeepSeek 精选 Top 5。

数据流：
    1. 优先读 webapp/cache/hot_picks.json，命中且未过期（12 小时）直接返回。
    2. 缓存未命中时，调用 hot_list.fetch_hot_list(size=20) 拿候选（这一步本身
       已经走 webapp/cache/hot_list.json 的 12h 缓存）。
    3. 若配置了 DeepSeek key，按下面的 PICK_USER_PROMPT_TEMPLATE 让模型从
       Top 20 里挑出 5 条，并解析模型返回的 JSON。
    4. 没有 key 或模型解析失败时，使用本地 mock 兜底（保持与上线后一致的字段
       结构，便于前端 / 联调）。
    5. 把结果写回 hot_picks.json（含来源、模型标识、prompt 备份），下次直接读。

之所以保留 prompt_messages，是方便上线 DeepSeek 接入前同事们直接看到当前
正在使用的范式，不必猜测。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import deepseek_client, hot_list
from .config_loader import load_config


logger = logging.getLogger(__name__)


PICKS_TTL_SECONDS = 12 * 60 * 60  # 12 小时
PICKS_TARGET = 5
CANDIDATE_TOP = 20

WEBAPP_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = WEBAPP_DIR / "cache"
PICKS_CACHE_FILE = CACHE_DIR / "hot_picks.json"

_LOCK = threading.Lock()


# ---------------- Prompt 范式（后续接入真实 DeepSeek 时直接生效） ---------------- #

PICK_SYSTEM_PROMPT = (
    "你是 Zmate（知乎 Mate），一名擅长信息筛选的中文 AI 助理。"
    "用户希望你从今天的知乎热榜 Top 20 里，挑出最「值得关注」的 5 条话题。"
    "你的判断标准是：是否具有公共讨论价值、是否对普通人有信息增量、"
    "是否能反映行业 / 政策 / 社会的真实趋势。"
)

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


# ---------------- DeepSeek / Mock ---------------- #

def _ask_deepseek(messages: List[Dict[str, str]]) -> Optional[str]:
    """调用 DeepSeek。未配置 key 或调用异常时返回 None，由调用方走 mock。"""
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
        return client.chat_once(messages, temperature=0.3)
    except Exception as exc:  # noqa: BLE001 - 网络/解析层全部兜底走 mock
        logger.warning("deepseek pick call failed: %s", exc)
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
    """优先用 DeepSeek 生成；失败 / 未配置时退回到基于 picks 的确定性模板。"""
    if not picks:
        return _mock_observation(picks)
    messages = build_observation_messages(picks)
    raw = _ask_deepseek(messages)
    if raw and raw.strip():
        return raw.strip()
    return _mock_observation(picks)


# ---------------- 对外入口 ---------------- #

def get_hot_picks(force_refresh: bool = False) -> Dict[str, Any]:
    """返回 5 条「值得关注的热点」+ 一段对应的「今日观察」。

    返回字段：
      - picks: 5 条命中候选的完整结构（包含 reason）
      - candidates: 本次模型可见的 Top 20 候选
      - observation: 基于真实 picks 生成的一段「今日观察」（DeepSeek 或本地模板）
      - hot_source: 热榜的来源（zhihu_open_api / disk-cache / mock 等）
      - model_used: deepseek 或 mock
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
                    # 老缓存里没有 observation，懒补一次；不影响 12h TTL
                    cached["observation"] = _generate_observation(picks)
                    _write_cache(cached)
                logger.info(
                    "hot_picks cache hit, age=%.1fh, model=%s",
                    max(0.0, (now - ts) / 3600.0),
                    cached.get("model_used"),
                )
                return {**cached, "cache": "hit"}

        hot = hot_list.fetch_hot_list(size=CANDIDATE_TOP)
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
        raw = _ask_deepseek(messages)

        picks: List[Dict[str, Any]] = []
        model_used = "mock"
        if raw:
            picks = _parse_picks(raw, candidates)
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
