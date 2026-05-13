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


def load_config() -> Dict[str, Any]:
    """合并三处来源：示例配置 < 本地配置 < 环境变量；全局密钥另行注入。"""
    base = _safe_load_json(EXAMPLE_CONFIG_PATH)
    base.update(_safe_load_json(LOCAL_CONFIG_PATH))

    env_overrides = {
        "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY"),
        "deepseek_base_url": os.environ.get("DEEPSEEK_BASE_URL"),
        "deepseek_model": os.environ.get("DEEPSEEK_MODEL"),
        "zhihu_app_key": os.environ.get("ZHIHU_APP_KEY"),
        "zhihu_oauth_app_id": os.environ.get("ZHIHU_OAUTH_APP_ID"),
        "zhihu_oauth_app_key": os.environ.get("ZHIHU_OAUTH_APP_KEY"),
        "zhihu_oauth_redirect_uri": os.environ.get("ZHIHU_OAUTH_REDIRECT_URI"),
        "flask_secret_key": os.environ.get("FLASK_SECRET_KEY"),
    }
    for k, v in env_overrides.items():
        if v:
            base[k] = v

    global_keys = _safe_load_json(GLOBAL_KEY_PATH)
    if "app_secret" in global_keys and not base.get("zhihu_app_secret"):
        base["zhihu_app_secret"] = global_keys["app_secret"]
    # 同时允许把 OAuth 凭据写在 ../config/API_KEY.json 里，便于和现有 app_secret 放一起。
    # 既支持 snake_case，也兼容知乎控制台拷贝过来的大写 APP_ID / APP_KEY 写法。
    for aliases, dst_key in (
        (("zhihu_oauth_app_id", "APP_ID", "app_id"), "zhihu_oauth_app_id"),
        (("zhihu_oauth_app_key", "APP_KEY", "app_key"), "zhihu_oauth_app_key"),
        (("zhihu_oauth_redirect_uri", "REDIRECT_URI", "redirect_uri"), "zhihu_oauth_redirect_uri"),
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
