"""统一加载项目所需的配置（从 config/API_KEY.json 与 webapp/config.json 合并）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


WEBAPP_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WEBAPP_DIR.parent
GLOBAL_KEY_PATH = PROJECT_ROOT / "config" / "API_KEY.json"
LOCAL_CONFIG_PATH = WEBAPP_DIR / "config.json"
EXAMPLE_CONFIG_PATH = WEBAPP_DIR / "config.example.json"


def _safe_load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_positive_int(value: Any) -> int | None:
    """把字符串 / 数字解析成正整数；失败或 <=0 一律返回 None。

    用于消化环境变量里的 timeout：传 "60" 是合法的，传 "abc" / "0" / "-1"
    都被静默丢弃，让 server.py 回落到 client 默认值，避免一个错配置把整个
    通道弄成立刻 timeout。
    """
    if value is None or value == "":
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def load_config() -> Dict[str, Any]:
    """合并三处来源：示例配置 < 本地配置 < 环境变量；全局密钥另行注入。"""
    base = _safe_load_json(EXAMPLE_CONFIG_PATH)
    base.update(_safe_load_json(LOCAL_CONFIG_PATH))

    env_overrides = {
        "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY"),
        "deepseek_base_url": os.environ.get("DEEPSEEK_BASE_URL"),
        "deepseek_model": os.environ.get("DEEPSEEK_MODEL"),
        # Moonshot 官方文档里同时用 KIMI_* 和 MOONSHOT_* 两套环境变量名，这里都认。
        "kimi_api_key": os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY"),
        "kimi_base_url": os.environ.get("KIMI_BASE_URL") or os.environ.get("MOONSHOT_BASE_URL"),
        "kimi_model": os.environ.get("KIMI_MODEL") or os.environ.get("MOONSHOT_MODEL"),
        "zhihu_app_key": os.environ.get("ZHIHU_APP_KEY"),
        "zhihu_oauth_app_id": os.environ.get("ZHIHU_OAUTH_APP_ID"),
        "zhihu_oauth_app_key": os.environ.get("ZHIHU_OAUTH_APP_KEY"),
        "zhihu_oauth_redirect_uri": os.environ.get("ZHIHU_OAUTH_REDIRECT_URI"),
        "flask_secret_key": os.environ.get("FLASK_SECRET_KEY"),
    }
    for k, v in env_overrides.items():
        if v:
            base[k] = v

    # 把三家对话模型的 timeout 收口到 `model_timeouts` 子字典里。
    # 优先级：config.example < webapp/config.json < 环境变量。
    raw_timeouts = base.get("model_timeouts")
    timeouts: Dict[str, int] = {}
    if isinstance(raw_timeouts, dict):
        for provider, value in raw_timeouts.items():
            parsed = _parse_positive_int(value)
            if parsed is not None:
                timeouts[str(provider).strip().lower()] = parsed
    for provider, env_key in (
        ("deepseek", "DEEPSEEK_TIMEOUT"),
        ("zhida", "ZHIDA_TIMEOUT"),
        ("kimi", "KIMI_TIMEOUT"),
    ):
        parsed = _parse_positive_int(os.environ.get(env_key))
        if parsed is not None:
            timeouts[provider] = parsed
    base["model_timeouts"] = timeouts

    global_keys = _safe_load_json(GLOBAL_KEY_PATH)
    if "app_secret" in global_keys and not base.get("zhihu_app_secret"):
        base["zhihu_app_secret"] = global_keys["app_secret"]
    # 同时允许把 OAuth 凭据 / 各家模型 key 写在 ../config/API_KEY.json 里，
    # 便于和现有 app_secret 放一起统一管理（这个文件本身已经被 .gitignore）。
    # 既支持 snake_case，也兼容控制台拷贝过来的大写写法（如知乎的 APP_ID）。
    # 注意：base 里已经有值（来自 webapp/config.json 或环境变量）就不覆盖，
    # 也就是说优先级始终是 example < 本地 config < 环境变量 < ……（这里兜底）。
    for aliases, dst_key in (
        (("zhihu_oauth_app_id", "APP_ID", "app_id"), "zhihu_oauth_app_id"),
        (("zhihu_oauth_app_key", "APP_KEY", "app_key"), "zhihu_oauth_app_key"),
        (("zhihu_oauth_redirect_uri", "REDIRECT_URI", "redirect_uri"), "zhihu_oauth_redirect_uri"),
        # 大模型 API key：让用户能把所有敏感凭据集中放在同一个 gitignore 文件里。
        (("kimi_api_key", "KIMI_API_KEY", "MOONSHOT_API_KEY"), "kimi_api_key"),
        (("deepseek_api_key", "DEEPSEEK_API_KEY"), "deepseek_api_key"),
    ):
        if base.get(dst_key):
            continue
        for alias in aliases:
            value = global_keys.get(alias)
            if value:
                base[dst_key] = str(value).strip()
                break

    base.pop("_comments", None)
    return base
