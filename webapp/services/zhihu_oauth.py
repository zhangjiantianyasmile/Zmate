"""知乎开放平台 OAuth 2.0 客户端封装。

接口规范参考 `document/api/oauth/` 下三份文档：

- 授权页：     GET  https://openapi.zhihu.com/authorize?app_id=...&redirect_uri=...&response_type=code
- 换 token： POST https://openapi.zhihu.com/access_token  (form-urlencoded)
- 拉资料：   GET  https://openapi.zhihu.com/user/info       (Bearer access_token)

注意：
    OAuth 这套用到的 `app_id` / `app_key` 与社区签名 API 的 `app_key` / `app_secret`
    是完全不同的凭据，需要在 https://www.zhihu.com/ring/moltbook 单独申请一组
    OAuth 应用。申请时填写的「知乎登录回调地址」必须与 `redirect_uri` 完全一致，
    包括协议、域名、端口、路径。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests


logger = logging.getLogger(__name__)


AUTHORIZE_URL = "https://openapi.zhihu.com/authorize"
ACCESS_TOKEN_URL = "https://openapi.zhihu.com/access_token"
# 注意：知乎 OAuth 文档对应文件叫 user_info.html，但里面写明真实 HTTP URL 是 `/user`，
# 不是 `/user/info`。早先写错过一次，这里已校正以避免再次踩坑。
USER_INFO_URL = "https://openapi.zhihu.com/user"


class ZhihuOAuthError(RuntimeError):
    """OAuth 流程中任何一步失败时抛出。"""


@dataclass
class ZhihuOAuthConfig:
    app_id: str
    app_key: str
    # 配置里的 redirect_uri 仅作 fallback / 默认值；运行时可以由调用方传入实际值，
    # 这样就能让本地 (127.0.0.1:5050) 和公网隧道 (bore.pub:17050) 共用同一份代码。
    redirect_uri: str = ""

    @property
    def is_ready(self) -> bool:
        # 只要 app_id / app_key 都有，就视为可用；redirect_uri 可以在运行时由 server 注入。
        return bool(self.app_id and self.app_key)


class ZhihuOAuthClient:
    def __init__(self, config: ZhihuOAuthConfig, timeout: int = 15) -> None:
        self.config = config
        self.timeout = timeout

    @property
    def is_ready(self) -> bool:
        return self.config.is_ready

    def _resolve_redirect_uri(self, override: Optional[str]) -> str:
        target = (override or self.config.redirect_uri or "").strip()
        if not target:
            raise ZhihuOAuthError("missing redirect_uri (neither config nor request provided one)")
        return target

    def build_authorize_url(
        self,
        state: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ) -> str:
        """拼出引导用户跳转的授权页地址。"""
        if not self.is_ready:
            raise ZhihuOAuthError("zhihu oauth not configured")
        params = {
            "app_id": self.config.app_id,
            "redirect_uri": self._resolve_redirect_uri(redirect_uri),
            "response_type": "code",
        }
        if state:
            params["state"] = state
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_token(
        self,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """用授权码换 access_token。返回 dict 至少含 access_token。"""
        if not self.is_ready:
            raise ZhihuOAuthError("zhihu oauth not configured")
        if not code:
            raise ZhihuOAuthError("missing authorization code")

        data = {
            "app_id": self.config.app_id,
            "app_key": self.config.app_key,
            "grant_type": "authorization_code",
            "redirect_uri": self._resolve_redirect_uri(redirect_uri),
            "code": code,
        }
        try:
            resp = requests.post(
                ACCESS_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ZhihuOAuthError(f"network error: {exc}") from exc

        if resp.status_code >= 400:
            raise ZhihuOAuthError(
                f"access_token http {resp.status_code}: {resp.text[:300]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ZhihuOAuthError(f"access_token bad json: {resp.text[:200]}") from exc

        # 部分平台会把错误也包在 200 里，做一层兜底解析。
        if isinstance(payload, dict) and payload.get("error"):
            raise ZhihuOAuthError(f"access_token error: {payload}")

        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not access_token:
            raise ZhihuOAuthError(f"access_token missing in response: {payload}")
        return payload

    def fetch_user_info(self, access_token: str) -> Dict[str, Any]:
        """拉取当前授权用户的基本信息。"""
        if not access_token:
            raise ZhihuOAuthError("missing access_token")

        try:
            resp = requests.get(
                USER_INFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ZhihuOAuthError(f"network error: {exc}") from exc

        if resp.status_code >= 400:
            raise ZhihuOAuthError(
                f"user_info http {resp.status_code}: {resp.text[:300]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ZhihuOAuthError(f"user_info bad json: {resp.text[:200]}") from exc

        # 文档样例字段是直接铺在顶层；少数实现会包一层 data。两种都吃。
        if isinstance(payload, dict) and "uid" not in payload and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict) or not payload.get("uid"):
            raise ZhihuOAuthError(f"user_info missing uid: {payload}")
        return payload


def build_client(cfg: Dict[str, Any]) -> ZhihuOAuthClient:
    """按 load_config() 的 dict 构造一个客户端。"""
    return ZhihuOAuthClient(
        ZhihuOAuthConfig(
            app_id=str(cfg.get("zhihu_oauth_app_id") or "").strip(),
            app_key=str(cfg.get("zhihu_oauth_app_key") or "").strip(),
            redirect_uri=str(cfg.get("zhihu_oauth_redirect_uri") or "").strip(),
        )
    )
