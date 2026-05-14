"""Moonshot Kimi Chat 客户端封装。

接口与 OpenAI Chat Completions 协议兼容：

- HTTP URL: https://api.moonshot.ai/v1/chat/completions
              （国内站可换成 https://api.moonshot.cn/v1）
- Method:   POST
- Headers:  Authorization: Bearer <api_key>

推荐模型档位（截至 2026 年）：
    - kimi-k2-thinking         K2 强制思考版（reasoning_content 一定有）
    - kimi-k2-thinking-turbo   K2 思考 · Turbo，256k 上下文、输出更快
    - kimi-k2.6-thinking       K2.6 思考版，K2 系列下线后的迁移目标
    （以 https://platform.kimi.ai/docs/models 上你账号实际可见的为准）

思考模型流式响应里会同时返回 `delta.content` 与 `delta.reasoning_content`，
我们把后者只写进日志、不向上 yield，避免「思考链」污染最终对话气泡——
这与 zhida_client.py 中处理直答深度思考模型的策略一致。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Generator, List, Optional

import requests

from .llm_logger import LLMCallLogger


logger = logging.getLogger(__name__)


# Moonshot 国内站（platform.moonshot.cn）与国际站（platform.moonshot.ai）的
# 账号体系完全独立，两边的 sk-... key 互不通用。默认指向国内站，匹配多数
# 中文用户的首充路径；如果你的 key 是从 platform.moonshot.ai 申请的，请在
# webapp/config.json 里把 kimi_base_url 改成 "https://api.moonshot.ai/v1"。
DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "kimi-k2-thinking-turbo"

# 单次 HTTP 请求超时（秒）。Kimi 思考模型首 token 较慢（要先吐 reasoning），
# 默认给到 90s，比 deepseek/zhida 的 60s 宽一档。集中作为唯一默认值来源。
DEFAULT_TIMEOUT = 90

# 显式枚举一份「我们愿意主动暴露给前端的模型」白名单，避免随便传一个 model
# 名就直接打到上游。新增模型时往这里加即可。
#
# 重要：Moonshot 不同账号能调的模型不同——free 账号通常只对 `moonshot-v1-*`
# 这类基础模型开放；`kimi-k2-thinking-*` 等思考模型需要在 platform.moonshot.cn
# 控制台充过值（变成付费层）才能调用，否则会返回 404 +
# "Not found ... or Permission denied"。这种情况不是模型 id 错，是权限问题。
#
# 想知道自己当前账号到底能调哪些，访问 webapp 的 `/api/zmate/kimi/models`
# 接口（仅在你主动请求时才会向 Moonshot 发 GET /v1/models）。
ALLOWED_MODELS = {
    # 思考模型（一般需付费层）
    "kimi-k2-thinking",
    "kimi-k2-thinking-turbo",
    "kimi-k2.6-thinking",
    # 通用对话 / 长上下文（K2 系列将于 2026-05-25 下线）
    "kimi-latest",
    "kimi-k2-turbo-preview",
    "kimi-k2-0905-preview",
    "kimi-k2-0711-preview",
    "kimi-k2.5",
    "kimi-k2.6",
    # Moonshot v1 基础对话（free 账号通常能调）
    "moonshot-v1-8k",
    "moonshot-v1-32k",
    "moonshot-v1-128k",
    "moonshot-v1-auto",
}


class KimiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    @property
    def is_ready(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _resolve_model(self, model: Optional[str]) -> str:
        if model and model in ALLOWED_MODELS:
            return model
        if model:
            logger.info("kimi: unknown model %r, fallback to %s", model, self.model)
        return self.model

    def _auth_hint(self) -> str:
        """401/403 时给到前端的诊断提示——把最常见的「站点 / key 不匹配」直接说穿。

        Moonshot 国内站（platform.moonshot.cn → api.moonshot.cn）和国际站
        （platform.moonshot.ai → api.moonshot.ai）的 sk- key **完全不互通**，
        但错误体只回 "Invalid Authentication"，新手很容易卡在这一步。
        """
        host = self.base_url
        if "moonshot.ai" in host:
            other_site = "platform.moonshot.cn → 把 kimi_base_url 改成 https://api.moonshot.cn/v1"
        elif "moonshot.cn" in host:
            other_site = "platform.moonshot.ai → 把 kimi_base_url 改成 https://api.moonshot.ai/v1"
        else:
            other_site = "另一个站点（.cn / .ai）"
        return (
            f"当前 base_url={host}；请确认 (1) key 没过期且未被禁用；"
            f"(2) key 申请的站点与 base_url 必须一致——若 key 申请自 {other_site}。"
        )

    def _model_perm_hint(self, current_model: str) -> str:
        """404 / resource_not_found 时的诊断提示。

        Moonshot 对返回体的措辞是 "Not found the model X or Permission denied"，
        实际上 99% 都是「模型 id 是对的，但你这个账号没开通调用权限」。最常见
        触发：free 账号去调思考类（kimi-k2-thinking-*）；需要充值切到付费层。
        """
        is_thinking = "thinking" in (current_model or "")
        free_safe = "moonshot-v1-8k / moonshot-v1-32k / moonshot-v1-128k"
        tip_pay = (
            "去 platform.moonshot.cn 充值（一般 ¥10 即开通付费层）后再调"
            if is_thinking
            else "去 platform.moonshot.cn 检查该模型是否对你的账号开通"
        )
        return (
            f"模型 {current_model!r} 未对当前账号开放（响应里出现 'or Permission denied' "
            f"通常是权限不足，不是 model id 错）。两条路：(A) {tip_pay}；"
            f"(B) 暂时在 webapp/config.json 里把 kimi_model 改成 free 账号能调的基础模型，"
            f"如 {free_safe}。也可以访问 /api/zmate/kimi/models 直接列出当前账号可用模型。"
        )

    def list_models(self) -> Dict[str, Any]:
        """主动列出当前账号能调的模型（代理 Moonshot 的 GET /v1/models）。

        仅在调用方主动调它时才会发请求；server.py 把它挂在 `/api/zmate/kimi/models`
        路由上，留作排障入口。返回上游原始 JSON（带 .data 列表），调用失败时
        抛 RuntimeError 让 server.py 那边返 5xx。
        """
        if not self.is_ready:
            raise RuntimeError("kimi_api_key 未配置，无法列模型")
        url = f"{self.base_url}/models"
        headers = self._headers()
        call_log = LLMCallLogger(
            provider="kimi",
            model="<list_models>",
            request_url=url,
            request_headers=headers,
            request_payload=None,
            stream=False,
        )
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.encoding = "utf-8"
            call_log.log_response_meta(resp.status_code, resp.headers)
            try:
                body = resp.json()
            except ValueError:
                call_log.log_response_body(resp.text)
                raise RuntimeError(f"kimi list_models http {resp.status_code} non-json")
            call_log.log_response_body(body)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"kimi list_models http {resp.status_code}: {str(body)[:200]}"
                )
            return body if isinstance(body, dict) else {"data": body}
        except Exception as exc:
            call_log.log_error(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            call_log.close()

    def chat_once(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.6,
    ) -> str:
        """非流式调用，返回完整 content。"""
        url = f"{self.base_url}/chat/completions"
        resolved_model = self._resolve_model(model)
        headers = self._headers()
        payload = {
            "model": resolved_model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        call_log = LLMCallLogger(
            provider="kimi",
            model=resolved_model,
            request_url=url,
            request_headers=headers,
            request_payload=payload,
            stream=False,
        )
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            # 同 chat_stream：覆盖 requests 对没有 charset 的响应做出的 Latin-1
            # 猜测，确保 resp.text / 后续 4xx 错误体的中文不会变成 mojibake。
            resp.encoding = "utf-8"
            call_log.log_response_meta(resp.status_code, resp.headers)
            if resp.status_code >= 400:
                call_log.log_response_body(resp.text)
                call_log.log_error(f"http {resp.status_code}")
                hint = ""
                if resp.status_code in (401, 403):
                    hint = f"\n诊断：{self._auth_hint()}"
                elif resp.status_code == 404:
                    hint = f"\n诊断：{self._model_perm_hint(resolved_model)}"
                raise RuntimeError(
                    f"kimi http {resp.status_code}: {resp.text[:200]}{hint}"
                )
            try:
                body = resp.json()
            except ValueError:
                call_log.log_response_body(resp.text)
                call_log.log_error("non-json response body")
                return ""
            call_log.log_response_body(body)
            choices = body.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""
            if content:
                call_log.log_stream_delta(content)
            return content
        except Exception as exc:
            call_log.log_error(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            call_log.close()

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.6,
    ) -> Generator[str, None, None]:
        """以 SSE 流的形式产生增量 token；`reasoning_content` 仅入日志。"""
        url = f"{self.base_url}/chat/completions"
        resolved_model = self._resolve_model(model)
        headers = self._headers()
        payload = {
            "model": resolved_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        call_log = LLMCallLogger(
            provider="kimi",
            model=resolved_model,
            request_url=url,
            request_headers=headers,
            request_payload=payload,
            stream=True,
        )
        reasoning_acc: List[str] = []
        try:
            with requests.post(
                url, headers=headers, json=payload, stream=True, timeout=self.timeout
            ) as resp:
                # Moonshot 的 SSE 响应头里 `Content-Type` 只写 "text/event-stream"，
                # 没带 charset。requests.iter_lines(decode_unicode=True) 在这种
                # 情况会按 RFC 2616 老默认 fall back 到 ISO-8859-1，把上游 UTF-8
                # 字节按 Latin-1 解出来——中文就会变成 "ä»¥ä¸æ¯" 这种 mojibake。
                # 这里手动锁成 utf-8，覆盖 requests 的猜测。
                resp.encoding = "utf-8"
                call_log.log_response_meta(resp.status_code, resp.headers)
                if resp.status_code >= 400:
                    err = resp.text[:500]
                    logger.warning("kimi http %s: %s", resp.status_code, err)
                    call_log.log_response_body(err)
                    call_log.log_error(f"http {resp.status_code}")
                    hint = ""
                    if resp.status_code in (401, 403):
                        hint = f"\n诊断：{self._auth_hint()}"
                    elif resp.status_code == 404:
                        hint = f"\n诊断：{self._model_perm_hint(resolved_model)}"
                    fallback = f"[Zmate 调用 Kimi 出错 {resp.status_code}：{err}]{hint}"
                    call_log.log_stream_delta(fallback)
                    yield fallback
                    return

                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    call_log.log_stream_raw_line(raw_line)
                    if raw_line.startswith(":"):
                        # SSE 心跳注释，跳过。
                        continue
                    if raw_line.startswith("data: "):
                        raw_line = raw_line[len("data: "):]
                    elif raw_line.startswith("data:"):
                        raw_line = raw_line[len("data:"):]
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    if raw_line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    err_obj = chunk.get("error")
                    if err_obj:
                        msg = (err_obj or {}).get("message") or "Kimi 服务错误"
                        call_log.log_error(f"upstream error: {msg}")
                        err_text = f"\n[Kimi 返回错误：{msg}]"
                        call_log.log_stream_delta(err_text)
                        yield err_text
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        reasoning_acc.append(str(reasoning))
                        logger.debug("kimi reasoning chunk: %s", str(reasoning)[:80])
                    content = delta.get("content")
                    if content:
                        call_log.log_stream_delta(content)
                        yield content
        except requests.RequestException as exc:
            logger.warning("kimi request failed: %s", exc)
            call_log.log_error(f"RequestException: {exc}")
            fallback = f"\n[Zmate 调用 Kimi 失败：{exc}]"
            call_log.log_stream_delta(fallback)
            yield fallback
        finally:
            summary_extra = {
                "reasoning_text_length": sum(len(r) for r in reasoning_acc),
                "reasoning_text": "".join(reasoning_acc),
            }
            call_log.close(summary_extra=summary_extra)


__all__ = [
    "KimiClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "ALLOWED_MODELS",
]
