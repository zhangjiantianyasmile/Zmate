"""Zmate webapp Flask 入口。

提供首页 / 文档详情 / Zmate 聊天的 API，并直接托管前端静态资源。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    stream_with_context,
)
from flask_cors import CORS

from services import (
    deepseek_client,
    hot_list,
    hot_picks,
    mock_data,
    search as search_service,
    zhida_client,
    zhihu_oauth,
)
from services.config_loader import load_config


# 可选的对话模型 provider；前端会用它做选择，后端据此路由。
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_ZHIDA = "zhida"
DEFAULT_PROVIDER = PROVIDER_ZHIDA


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("zmate")


WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
# Flask session 用来在「跳转去知乎授权 → 回调」之间存 state 与登录用户信息。
# 留空时回落到进程级随机串：能跑通本地体验，但服务重启会让所有人重新登录。
_session_secret = (load_config().get("flask_secret_key") or "").strip()
app.secret_key = _session_secret or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # OAuth 回跳是 top-level navigation，Lax 既能带上 cookie 又能挡跨站 POST。
    SESSION_COOKIE_SAMESITE="Lax",
)
# 允许前端带 cookie 调 /api/auth/me 等接口（例如把 apiBase 指到 bore.pub 的场景）。
CORS(app, supports_credentials=True)


def _build_deepseek() -> deepseek_client.DeepSeekClient:
    cfg = load_config()
    return deepseek_client.DeepSeekClient(
        api_key=cfg.get("deepseek_api_key", "") or "",
        base_url=cfg.get("deepseek_base_url") or "https://api.deepseek.com/v1",
        model=cfg.get("deepseek_model") or "deepseek-chat",
    )


def _build_zhida() -> zhida_client.ZhidaClient:
    cfg = load_config()
    return zhida_client.ZhidaClient(
        access_secret=cfg.get("zhihu_app_secret", "") or "",
    )


def _model_catalog() -> List[Dict[str, Any]]:
    """对外暴露的模型清单，供前端渲染下拉选择。"""
    cfg = load_config()
    deepseek_ready = bool(cfg.get("deepseek_api_key"))
    zhida_ready = bool(cfg.get("zhihu_app_secret"))
    return [
        {
            "provider": PROVIDER_ZHIDA,
            "model": "zhida-thinking-1p5",
            "label": "知乎直答 · 深度思考",
            "short": "直答·思考",
            "description": "走知乎开放平台的 zhida-thinking-1p5 模型，适合需要推理过程的复杂提问。",
            "ready": zhida_ready,
            "default": True,
        },
        {
            "provider": PROVIDER_ZHIDA,
            "model": "zhida-fast-1p5",
            "label": "知乎直答 · 快速回答",
            "short": "直答·快速",
            "description": "更快的直答档位，适合追求即时反馈的简单问题。",
            "ready": zhida_ready,
            "default": False,
        },
        {
            "provider": PROVIDER_DEEPSEEK,
            "model": "deepseek-chat",
            "label": "DeepSeek Chat",
            "short": "DeepSeek",
            "description": (
                "OpenAI 兼容协议的 deepseek-chat 模型；当前未配置 API Key，"
                "选择后会回退到本地 mock 流式回复。"
            ),
            "ready": deepseek_ready,
            "default": False,
        },
    ]


# ---------------- Pages ---------------- #

@app.route("/")
def index_page() -> Response:
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/detail.html")
def detail_page() -> Response:
    return send_from_directory(str(STATIC_DIR), "detail.html")


@app.route("/health")
def health() -> Response:
    cfg = load_config()
    oauth_client = zhihu_oauth.build_client(cfg)
    return jsonify(
        {
            "status": "ok",
            "deepseek_ready": bool(cfg.get("deepseek_api_key")),
            "zhihu_app_secret_loaded": bool(cfg.get("zhihu_app_secret")),
            "zhihu_app_key_loaded": bool(cfg.get("zhihu_app_key")),
            "zhihu_oauth_ready": oauth_client.is_ready,
            "zhihu_oauth_redirect_uri": cfg.get("zhihu_oauth_redirect_uri") or "",
            "zhihu_oauth_redirect_uri_runtime": _resolve_redirect_uri(cfg),
        }
    )


# ---------------- Auth (Zhihu OAuth 2.0) ---------------- #

# 受信任的回跳目标白名单（避免开放重定向）：默认只允许站内相对路径。
def _safe_post_login_redirect(target: str) -> str:
    if not target:
        return "/"
    parsed = urlparse(target)
    # 不允许带 scheme/netloc 的外链，避免 ?next=https://evil.com 这种攻击。
    if parsed.scheme or parsed.netloc:
        return "/"
    if not target.startswith("/"):
        return "/"
    return target


def _current_user() -> Dict[str, Any] | None:
    user = session.get("user")
    return user if isinstance(user, dict) else None


def _resolve_redirect_uri(cfg: Dict[str, Any]) -> str:
    """决定本次 OAuth 流程使用的 redirect_uri。

    优先级：
      1. 配置/环境变量里显式写的 zhihu_oauth_redirect_uri（适合反向代理后面 Host
         头不可靠的场景，比如背后挂着 Nginx + 自定义域名时强制锁死）；
      2. 否则用本次请求的 scheme + Host 自动拼出 `<scheme>://<host>/auth/zhihu/callback`。
         这样：
         - `python server.py` 本地访问时拼出 `http://127.0.0.1:5050/auth/zhihu/callback`
         - 用户经 bore.pub 访问时浏览器送来的 Host 是 `bore.pub:17050`，
           自动拼出 `http://bore.pub:17050/auth/zhihu/callback`
         同一份代码无需为不同入口改配置。
    """
    pinned = (cfg.get("zhihu_oauth_redirect_uri") or "").strip()
    if pinned:
        return pinned
    # request.host_url 形如 "http://bore.pub:17050/"，拼上回调路径即可。
    # 反向代理（Nginx / Cloudflare 等）会通过 X-Forwarded-* 头告诉我们真正的入口域名/协议；
    # Flask 默认不信任这些头，下面手动读一下，命中时优先用。
    proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("X-Forwarded-Host") or request.host or "").split(",")[0].strip()
    if not host:
        # 极端兜底：没有任何线索时回退到 host_url（也可能是 localhost）。
        return request.host_url.rstrip("/") + "/auth/zhihu/callback"
    return f"{proto}://{host}/auth/zhihu/callback"


@app.route("/auth/zhihu/login")
def auth_zhihu_login() -> Response:
    """生成 state 并把浏览器重定向到知乎授权页。"""
    cfg = load_config()
    client = zhihu_oauth.build_client(cfg)
    if not client.is_ready:
        return jsonify(
            {
                "error": "zhihu_oauth_not_configured",
                "message": (
                    "请在 config/API_KEY.json（APP_ID / APP_KEY）或 webapp/config.json"
                    "（zhihu_oauth_app_id / zhihu_oauth_app_key）里配置后再使用知乎登录。"
                ),
            }
        ), 503

    state = secrets.token_urlsafe(24)
    next_url = _safe_post_login_redirect(request.args.get("next") or "")
    redirect_uri = _resolve_redirect_uri(cfg)

    session["oauth_state"] = state
    session["oauth_next"] = next_url
    # 把本次握手用的 redirect_uri 锁进 session：换 token 时必须用同一个串，
    # 否则会被知乎的 OAuth 校验拒绝。
    session["oauth_redirect_uri"] = redirect_uri
    logger.info("zhihu oauth login -> redirect_uri=%s", redirect_uri)
    return redirect(client.build_authorize_url(state=state, redirect_uri=redirect_uri))


@app.route("/auth/zhihu/callback")
def auth_zhihu_callback() -> Response:
    """OAuth 回调：校验 state、用 code 换 token、拉用户信息、写入 session。"""
    cfg = load_config()
    client = zhihu_oauth.build_client(cfg)
    if not client.is_ready:
        return jsonify({"error": "zhihu_oauth_not_configured"}), 503

    err = request.args.get("error")
    if err:
        logger.warning("zhihu oauth callback error: %s", err)
        return _render_auth_error(f"知乎授权被拒绝：{err}", status=400)

    expected_state = session.pop("oauth_state", None)
    next_url = _safe_post_login_redirect(session.pop("oauth_next", "") or "")
    redirect_uri = session.pop("oauth_redirect_uri", "") or _resolve_redirect_uri(cfg)

    # 知乎 OAuth 当前实现不会把我们传过去的 state 原样回传（文档写了但实现没跟上），
    # 所以这里采用「有就严格校验，没有就降级到警告」的策略：
    # - 防 CSRF 主要依赖知乎对 redirect_uri 的注册白名单严格校验（攻击者无法把回调落到自己域名）；
    # - 如果未来知乎补上 state 透传，我们这里依旧能抓到不一致的情况。
    state = request.args.get("state") or ""
    if state:
        if not expected_state or state != expected_state:
            return _render_auth_error("登录态校验失败（state 不匹配），请重新发起登录。", status=400)
    else:
        if expected_state:
            logger.warning(
                "zhihu oauth callback: provider did not echo back state (expected=%s)",
                expected_state,
            )

    # 知乎实际回调用的参数名是 authorization_code，而文档里写的是 code，两个都接受。
    code = (
        request.args.get("code")
        or request.args.get("authorization_code")
        or ""
    ).strip()
    if not code:
        return _render_auth_error("缺少授权码 code，回调链接不完整。", status=400)

    try:
        token_payload = client.exchange_token(code, redirect_uri=redirect_uri)
        access_token = token_payload.get("access_token", "")
        user_info = client.fetch_user_info(access_token)
    except zhihu_oauth.ZhihuOAuthError as exc:
        logger.warning("zhihu oauth failed: %s", exc)
        return _render_auth_error(f"知乎登录失败：{exc}", status=502)

    expires_in = int(token_payload.get("expires_in") or 0)
    session["user"] = {
        "uid": user_info.get("uid"),
        "name": user_info.get("fullname") or "知乎用户",
        "avatar": user_info.get("avatar_path") or "",
        "headline": user_info.get("headline") or "",
        "gender": user_info.get("gender") or "",
    }
    session["zhihu_access_token"] = access_token
    session["zhihu_token_expires_at"] = int(time.time()) + expires_in if expires_in else 0
    session.permanent = True
    logger.info("zhihu oauth ok uid=%s name=%s", user_info.get("uid"), user_info.get("fullname"))
    return redirect(next_url or "/")


@app.route("/auth/zhihu/logout", methods=["GET", "POST"])
def auth_zhihu_logout() -> Response:
    """清掉本地 session（不会回收知乎那边的 access_token）。"""
    session.pop("user", None)
    session.pop("zhihu_access_token", None)
    session.pop("zhihu_token_expires_at", None)
    if request.method == "GET":
        return redirect(_safe_post_login_redirect(request.args.get("next") or "/"))
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def api_auth_me() -> Response:
    """前端用来判断当前是否已登录、渲染头像和昵称。"""
    cfg = load_config()
    client = zhihu_oauth.build_client(cfg)
    user = _current_user()
    return jsonify(
        {
            "authenticated": bool(user),
            "user": user,
            "oauth_ready": client.is_ready,
            "login_url": "/auth/zhihu/login" if client.is_ready else None,
            "logout_url": "/auth/zhihu/logout" if user else None,
        }
    )


def _render_auth_error(message: str, status: int = 400) -> Response:
    """登录流程出错时回一个简易页面，带「返回首页」按钮，调试更友好。"""
    safe_msg = (message or "登录失败").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!DOCTYPE html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Zmate · 登录失败</title>
<style>
  body {{ font-family: -apple-system, \"PingFang SC\", \"Microsoft YaHei\", sans-serif;
         background: #f6f7f9; margin: 0; min-height: 100vh; display: flex;
         align-items: center; justify-content: center; }}
  .card {{ background: #fff; padding: 28px 32px; border-radius: 12px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.06); max-width: 420px; }}
  h1 {{ font-size: 18px; margin: 0 0 12px; color: #d4380d; }}
  p {{ font-size: 14px; color: #444; line-height: 1.6; margin: 0 0 18px; }}
  a.btn {{ display: inline-block; padding: 8px 18px; border-radius: 18px;
          background: #1772f6; color: #fff; text-decoration: none; font-size: 14px; }}
</style></head><body>
  <div class=\"card\">
    <h1>知乎账号登录失败</h1>
    <p>{safe_msg}</p>
    <a class=\"btn\" href=\"/\">返回首页</a>
  </div>
</body></html>"""
    return Response(html, status=status, mimetype="text/html; charset=utf-8")


# ---------------- Feed APIs ---------------- #

@app.route("/api/feed")
def api_feed() -> Response:
    seed = request.args.get("seed", type=int)
    size = max(4, min(request.args.get("size", default=12, type=int), 40))
    items = mock_data.generate_feed(seed=seed, size=size)
    return jsonify(
        {
            "items": items,
            "categories": mock_data.generate_categories(),
            "filters": mock_data.generate_left_filters(),
            "promotions": mock_data.generate_promotions(),
            "fetched_at": int(time.time()),
        }
    )


@app.route("/api/hot")
def api_hot() -> Response:
    size = max(1, min(request.args.get("size", default=10, type=int), 30))
    page = max(0, request.args.get("page", default=0, type=int))
    refresh = request.args.get("refresh", default="0") == "1"
    payload = hot_list.fetch_hot_list(size=size, force_refresh=refresh, page=page)
    return jsonify(payload)


@app.route("/api/document/<doc_id>")
def api_document(doc_id: str) -> Response:
    doc = mock_data.generate_document_detail(doc_id)
    return jsonify(doc)


@app.route("/api/search")
def api_search() -> Response:
    """知乎站内搜索代理。Query 必填；Count 默认 10，最大 10。"""
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    count = request.args.get("count", default=10, type=int) or 10
    payload = search_service.search(query=query, count=count)
    return jsonify(payload)


# ---------------- Zmate APIs ---------------- #

@app.route("/api/zmate/news")
def api_zmate_news() -> Response:
    """返回 Zmate 精选的『值得关注的热点』Top 5。

    数据流：知乎热榜 Top 20（webapp/cache/hot_list.json） -> DeepSeek 选 5
    -> 写入 webapp/cache/hot_picks.json（12h 缓存）。无 DeepSeek key 时用
    本地 mock 兜底，prompt 范式保持一致。
    """
    refresh = request.args.get("refresh", default="0") == "1"
    payload = hot_picks.get_hot_picks(force_refresh=refresh)
    return jsonify(
        {
            "summary": "Zmate 帮你从知乎热榜里挑出 5 条最值得关注的话题",
            "picks": payload.get("picks", []),
            "candidates": payload.get("candidates", []),
            "observation": payload.get("observation", ""),
            "source": payload.get("hot_source"),
            "model_used": payload.get("model_used"),
            "cache": payload.get("cache"),
            "fetched_at": payload.get("fetched_at"),
        }
    )


@app.route("/api/zmate/models")
def api_zmate_models() -> Response:
    """暴露 Zmate 当前支持的模型清单，包含可用性（ready）与默认项。"""
    catalog = _model_catalog()
    default = next((m for m in catalog if m.get("default")), catalog[0] if catalog else None)
    return jsonify(
        {
            "models": catalog,
            "default": {
                "provider": (default or {}).get("provider"),
                "model": (default or {}).get("model"),
            } if default else None,
        }
    )


def _resolve_provider(req_provider: str, req_model: str) -> Dict[str, str]:
    """把前端传入的 provider/model 归一化到我们支持的范围内。"""
    provider = (req_provider or "").strip().lower()
    model = (req_model or "").strip()

    if provider not in (PROVIDER_DEEPSEEK, PROVIDER_ZHIDA):
        # 兼容前端只传 model 的情况：根据前缀猜测 provider。
        if model.startswith("zhida"):
            provider = PROVIDER_ZHIDA
        elif model.startswith("deepseek"):
            provider = PROVIDER_DEEPSEEK
        else:
            provider = DEFAULT_PROVIDER

    if provider == PROVIDER_ZHIDA and model not in zhida_client.ALLOWED_MODELS:
        model = zhida_client.DEFAULT_MODEL
    if provider == PROVIDER_DEEPSEEK and not model:
        model = "deepseek-chat"
    return {"provider": provider, "model": model}


@app.route("/api/zmate/chat", methods=["POST"])
def api_zmate_chat() -> Response:
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    history: List[Dict[str, str]] = body.get("messages") or []
    document = body.get("document")
    extra_context = body.get("context") or ""
    selection = _resolve_provider(body.get("provider") or "", body.get("model") or "")
    provider = selection["provider"]
    model = selection["model"]

    cleaned: List[Dict[str, str]] = []
    for m in history:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})

    system_prompt = deepseek_client.SYSTEM_PROMPT
    if document:
        doc_summary = (
            f"\n\n[当前用户正在阅读的文档信息]\n"
            f"标题：{document.get('title','')}\n"
            f"作者：{document.get('author','')}\n"
            f"内容摘要：{document.get('excerpt','')}\n"
        )
        system_prompt += doc_summary
    if extra_context:
        system_prompt += f"\n\n[额外上下文]\n{extra_context}\n"

    full_messages = [{"role": "system", "content": system_prompt}] + cleaned

    def _make_generator():
        if provider == PROVIDER_ZHIDA:
            zclient = _build_zhida()
            if zclient.is_ready:
                logger.info("zmate chat -> zhida (%s)", model)
                return zclient.chat_stream(full_messages, model=model), "zhida"
            logger.info("zmate chat -> zhida fallback to mock (no app_secret)")
            return deepseek_client.mock_chat_stream(cleaned, document=document), "mock"

        dclient = _build_deepseek()
        if dclient.is_ready:
            logger.info("zmate chat -> deepseek (%s)", dclient.model)
            return dclient.chat_stream(full_messages), "deepseek"
        logger.info("zmate chat -> deepseek fallback to mock (no api key)")
        return deepseek_client.mock_chat_stream(cleaned, document=document), "mock"

    def event_stream():
        try:
            generator, used = _make_generator()
            yield "data: " + json.dumps(
                {"meta": {"provider": provider, "model": model, "served_by": used}},
                ensure_ascii=False,
            ) + "\n\n"
            for chunk in generator:
                if not chunk:
                    continue
                yield "data: " + json.dumps({"delta": chunk}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        except Exception as exc:
            logger.exception("zmate chat failed")
            yield "data: " + json.dumps(
                {"delta": f"\n[Zmate 内部出错：{exc}]", "done": True},
                ensure_ascii=False,
            ) + "\n\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------- Static fallback ---------------- #

@app.route("/<path:path>")
def static_proxy(path: str) -> Response:
    target = STATIC_DIR / path
    if target.is_file():
        return send_from_directory(str(STATIC_DIR), path)
    return send_from_directory(str(STATIC_DIR), "index.html")


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5050"))
    debug = os.environ.get("DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
