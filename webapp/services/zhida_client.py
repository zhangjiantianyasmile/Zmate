"""知乎直答（Zhida）Chat 客户端封装。

接口规范参考 `document/api/developer/start.md` 中「直答 API」一节：

- HTTP URL: https://developer.zhihu.com/v1/chat/completions
- Method:   POST
- Headers:
    Authorization: Bearer <access_secret>
    X-Request-Timestamp: <unix_seconds>
- Body:     { model, messages, stream }

支持的模型档位：
    - zhida-fast-1p5      快速回答
    - zhida-thinking-1p5  深度思考
    - zhida-agent         智能思考

流式响应遵循 SSE 协议（与 OpenAI 兼容），消费 `delta.content` 片段；
若服务端额外返回 `delta.reasoning_content`（思考模型的过程），仅作日志，
不直接拼接进对话气泡，避免污染最终回答。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, Generator, List, Optional

import requests

from .llm_logger import LLMCallLogger


logger = logging.getLogger(__name__)


ZHIDA_CHAT_URL = "https://developer.zhihu.com/v1/chat/completions"

# 默认主推「深度思考」，体感更接近 Zmate 的定位。
DEFAULT_MODEL = "zhida-thinking-1p5"

ALLOWED_MODELS = {
    "zhida-fast-1p5",
    "zhida-thinking-1p5",
    "zhida-agent",
}

# 单次 HTTP 请求超时（秒）。集中暴露成模块级常量，便于 server.py 在
# config 里未配置时回落到这里，避免「默认值」散落在多个文件。
DEFAULT_TIMEOUT = 120


class ZhidaClient:
    def __init__(
        self,
        access_secret: str,
        base_url: str = ZHIDA_CHAT_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.access_secret = (access_secret or "").strip()
        self.base_url = base_url
        self.timeout = timeout

    @property
    def is_ready(self) -> bool:
        return bool(self.access_secret)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_secret}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        }

    def _resolve_model(self, model: Optional[str]) -> str:
        if model and model in ALLOWED_MODELS:
            return model
        if model:
            logger.info("zhida: unknown model %r, fallback to %s", model, DEFAULT_MODEL)
        return DEFAULT_MODEL

    @staticmethod
    def _normalize_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """把不被知乎直答接受的 `role=system` 消息前置/合并进首条 user。

        实测（2026-05）：知乎直答 `https://developer.zhihu.com/v1/chat/completions`
        只承认 `user` / `assistant` 两种 role；一旦 `messages` 里出现任何
        `role=system`，上游会直接返回 200 + `{"role":"assistant"}` 的空响应
        （流式则只下发 `delta:{}` + `finish_reason: stop`），既不报错也不带
        `error.message`，看上去就像「模型啥也没说」。

        我们的对接层（server.py）按 OpenAI 风格在 `messages` 头部塞了角色提示
        / 文档摘要 / 额外上下文等 `system` 内容，所以必须在客户端这里把它们
        改写成 user 前缀，否则前端永远只能看到「Zmate 没有产生有效回复」。

        合并规则：
          1. 顺序保留所有 `system` 消息的 content，拼成一段以
             `[系统提示]\n...` 包裹的前缀；
          2. 把该前缀贴到原始 messages 里 **第一条 user** 消息的 content 前面；
          3. 如果整段对话压根没有 user 消息（极端情况，仅 system），就把这段
             前缀本身当作单条 user 消息发出去；
          4. assistant / 其他合法 role 原样保留，相对顺序不变。
        """
        if not messages:
            return []

        system_parts: List[str] = []
        rest: List[Dict[str, str]] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = (m.get("role") or "").strip().lower()
            content = m.get("content") or ""
            if role == "system":
                if content.strip():
                    system_parts.append(content.strip())
                continue
            if role in ("user", "assistant") and content:
                rest.append({"role": role, "content": content})

        if not system_parts:
            return rest

        prefix = "[系统提示]\n" + "\n\n".join(system_parts) + "\n\n[用户提问]\n"

        for idx, m in enumerate(rest):
            if m.get("role") == "user":
                merged = dict(m)
                merged["content"] = prefix + m.get("content", "")
                rest[idx] = merged
                return rest

        # 整段对话只有 system（比如调用方拿 zhida 当一次性单 prompt 用），
        # 把这段前缀本身降级成一条 user 消息（去掉末尾的 `[用户提问]` 标记）。
        only_system = "[系统提示]\n" + "\n\n".join(system_parts)
        return [{"role": "user", "content": only_system}]

    def chat_once(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
    ) -> str:
        resolved_model = self._resolve_model(model)
        normalized = self._normalize_messages(messages)
        payload = {
            "model": resolved_model,
            "messages": normalized,
            "stream": False,
        }
        headers = self._headers()
        call_log = LLMCallLogger(
            provider="zhida",
            model=resolved_model,
            request_url=self.base_url,
            request_headers=headers,
            request_payload=payload,
            stream=False,
        )
        try:
            resp = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            call_log.log_response_meta(resp.status_code, resp.headers)
            if resp.status_code >= 400:
                call_log.log_response_body(resp.text)
                call_log.log_error(f"http {resp.status_code}")
                raise RuntimeError(
                    f"zhida http {resp.status_code}: {resp.text[:200]}"
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
            # 让 SUMMARY 里的 final_text 反映非流式调用最终拿到的内容。
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
    ) -> Generator[str, None, None]:
        """以增量形式 yield `content` 文本片段。

        - 跳过 `: keep-alive` 心跳注释行；
        - 遇到 `data: [DONE]` 结束；
        - `delta.reasoning_content` 仅记录日志，不向上抛出。
        """
        resolved_model = self._resolve_model(model)
        normalized = self._normalize_messages(messages)
        payload = {
            "model": resolved_model,
            "messages": normalized,
            "stream": True,
        }
        headers = self._headers()
        # 流式调用：用 LLMCallLogger 把每条原始 SSE 行 + 拼接后的 final_text
        # 一起落盘，方便排查「上游只回了 reasoning_content、前端没 delta」之类问题。
        call_log = LLMCallLogger(
            provider="zhida",
            model=resolved_model,
            request_url=self.base_url,
            request_headers=headers,
            request_payload=payload,
            stream=True,
        )
        reasoning_acc: List[str] = []
        try:
            with requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=self.timeout,
            ) as resp:
                call_log.log_response_meta(resp.status_code, resp.headers)
                if resp.status_code >= 400:
                    err = resp.text[:500]
                    logger.warning("zhida http %s: %s", resp.status_code, err)
                    call_log.log_response_body(err)
                    call_log.log_error(f"http {resp.status_code}")
                    fallback = f"[Zmate 调用直答出错 {resp.status_code}：{err}]"
                    call_log.log_stream_delta(fallback)
                    yield fallback
                    return

                buffer = bytearray()
                for raw in resp.iter_content(chunk_size=None):
                    if not raw:
                        continue
                    buffer.extend(raw)
                    while True:
                        idx = buffer.find(b"\n")
                        if idx < 0:
                            break
                        line_bytes = bytes(buffer[:idx])
                        del buffer[: idx + 1]
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        call_log.log_stream_raw_line(line)
                        if line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            line = line[len("data:"):].strip()
                        if not line:
                            continue
                        if line == "[DONE]":
                            return
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug("zhida: skip non-json line: %s", line[:120])
                            continue

                        err_obj = chunk.get("error")
                        if err_obj:
                            msg = (err_obj or {}).get("message") or "直答服务错误"
                            call_log.log_error(f"upstream error: {msg}")
                            err_text = f"\n[直答返回错误：{msg}]"
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
                            logger.debug("zhida reasoning chunk: %s", str(reasoning)[:80])
                        content = delta.get("content")
                        if content:
                            call_log.log_stream_delta(content)
                            yield content
        except requests.RequestException as exc:
            logger.warning("zhida request failed: %s", exc)
            call_log.log_error(f"RequestException: {exc}")
            fallback = f"\n[Zmate 调用直答失败：{exc}]"
            call_log.log_stream_delta(fallback)
            yield fallback
        finally:
            # 把这次调用里拿到的 reasoning_content 合并写一份，便于排查
            # 「只回了思考过程、没回最终答案」的情况——这是前端出现
            # 「Zmate 没有产生有效回复」最常见的根因。
            summary_extra = {
                "reasoning_text_length": sum(len(r) for r in reasoning_acc),
                "reasoning_text": "".join(reasoning_acc),
            }
            call_log.close(summary_extra=summary_extra)
