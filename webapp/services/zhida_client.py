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


logger = logging.getLogger(__name__)


ZHIDA_CHAT_URL = "https://developer.zhihu.com/v1/chat/completions"

# 默认主推「深度思考」，体感更接近 Zmate 的定位。
DEFAULT_MODEL = "zhida-thinking-1p5"

ALLOWED_MODELS = {
    "zhida-fast-1p5",
    "zhida-thinking-1p5",
    "zhida-agent",
}


class ZhidaClient:
    def __init__(
        self,
        access_secret: str,
        base_url: str = ZHIDA_CHAT_URL,
        timeout: int = 60,
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

    def chat_once(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
    ) -> str:
        payload = {
            "model": self._resolve_model(model),
            "messages": messages,
            "stream": False,
        }
        resp = requests.post(
            self.base_url,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"zhida http {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return msg.get("content") or ""

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
        payload = {
            "model": self._resolve_model(model),
            "messages": messages,
            "stream": True,
        }
        try:
            with requests.post(
                self.base_url,
                headers=self._headers(),
                json=payload,
                stream=True,
                timeout=self.timeout,
            ) as resp:
                if resp.status_code >= 400:
                    err = resp.text[:500]
                    logger.warning("zhida http %s: %s", resp.status_code, err)
                    yield f"[Zmate 调用直答出错 {resp.status_code}：{err}]"
                    return

                # 注意：必须按字节累积再解码 UTF-8，否则 requests 在按 chunk
                # 解码时会切断中文等多字节字符，导致 JSON 行残缺。
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
                            yield f"\n[直答返回错误：{msg}]"
                            continue

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            logger.debug("zhida reasoning chunk: %s", str(reasoning)[:80])
                        content = delta.get("content")
                        if content:
                            yield content
        except requests.RequestException as exc:
            logger.warning("zhida request failed: %s", exc)
            yield f"\n[Zmate 调用直答失败：{exc}]"
