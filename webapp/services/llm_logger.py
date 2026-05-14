"""大模型调用日志：每次调用按 `<模型>-<时间戳>.log` 落到 webapp/logs/。

目的：
    线上经常出现「Zmate 没有产生有效回复」之类的兜底提示，但很难复现，
    因为我们看不到上游模型真实的入参与原始 SSE 输出。把每次调用的请求体、
    响应头、流式原始行、拼接后的最终文本统一记到磁盘后，排查就只需要按
    时间戳找对应文件即可。

设计要点：
    1. 一次调用对应一个文件，文件名形如 `zhida-thinking-1p5-20260513-211045-238.log`，
       便于按模型 / 时间快速定位；
    2. 请求中的 `Authorization`、`X-API-Key` 等敏感头会被脱敏；
    3. 流式调用既记录每一条原始 SSE 行（便于看到上游真正给了什么），
       也累积 yield 出去的 `content` 片段，最终汇总成 `final_text`；
    4. 写文件失败不会影响主流程——所有 OSError 都被吞掉只打一条 warning。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


WEBAPP_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = WEBAPP_DIR / "logs"

# 文件名安全字符：保留字母/数字/`.`/`_`/`-`，其他统一替换成 `-`。
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")

# 需要脱敏的 header 名（小写比较）。
_SENSITIVE_HEADERS = {"authorization", "x-api-key", "api-key", "cookie"}

_LOCK = threading.Lock()


def _safe_filename_part(text: str) -> str:
    text = (text or "unknown").strip()
    text = _SAFE_NAME.sub("-", text)
    return text or "unknown"


def _redact_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    if not headers:
        return {}
    out: Dict[str, str] = {}
    for k, v in headers.items():
        if (k or "").lower() in _SENSITIVE_HEADERS:
            value = str(v or "")
            if len(value) > 12:
                out[k] = value[:6] + "***" + value[-4:]
            else:
                out[k] = "***"
        else:
            out[k] = str(v)
    return out


class LLMCallLogger:
    """单次大模型调用的日志器。

    生命周期：构造时立即建文件并写入请求元信息；调用方在合适时机喂入响应
    信息；最后必须 `close()`（建议放在 try/finally 中，确保流式 generator
    被打断时也能正确收尾）。
    """

    def __init__(
        self,
        provider: str,
        model: str,
        request_url: str,
        request_headers: Optional[Dict[str, str]] = None,
        request_payload: Optional[Any] = None,
        stream: bool = False,
        extra: Optional[Dict[str, Any]] = None,
        name_prefix: Optional[str] = None,
    ) -> None:
        self.provider = provider or "unknown"
        self.model = model or "unknown"
        self.stream = stream
        self.started_at = time.time()

        ts_struct = datetime.fromtimestamp(self.started_at)
        ts_text = ts_struct.strftime("%Y%m%d-%H%M%S")
        ms = int((self.started_at - int(self.started_at)) * 1000)
        safe_model = _safe_filename_part(self.model)
        # 加 4 位随机/序列后缀，避免同一毫秒并发请求重名（thread id 截断即可）。
        thread_suffix = str(threading.get_ident())[-4:]
        # `name_prefix` 允许调用方在文件名最前面加上场景标签，比如
        # "hotpicks-pick"，让一类批处理日志能用通配符快速捞出来，但模型 id
        # 仍然保留在文件名中、grep 单一模型时不影响命中。
        prefix_part = f"{_safe_filename_part(name_prefix)}-" if name_prefix else ""
        filename = f"{prefix_part}{safe_model}-{ts_text}-{ms:03d}-{thread_suffix}.log"

        self.path: Optional[Path] = None
        self._fp = None
        try:
            with _LOCK:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path = LOG_DIR / filename
            self._fp = self.path.open("a", encoding="utf-8")
        except OSError as exc:
            logger.warning("llm_logger open failed (model=%s): %s", self.model, exc)
            self.path = None
            self._fp = None

        self._stream_chunks: List[str] = []
        self._raw_line_count = 0
        self._closed = False

        meta: Dict[str, Any] = {
            "started_at": ts_struct.isoformat(timespec="milliseconds"),
            "provider": self.provider,
            "model": self.model,
            "stream": stream,
            "request": {
                "url": request_url,
                "headers": _redact_headers(request_headers),
                "payload": request_payload,
            },
        }
        if extra:
            meta["extra"] = extra
        self._write_section("REQUEST", meta)

    # ---------------- 内部工具 ---------------- #

    def _write_section(self, title: str, data: Any) -> None:
        if not self._fp:
            return
        try:
            self._fp.write(f"\n===== {title} =====\n")
            if isinstance(data, str):
                self._fp.write(data)
            else:
                self._fp.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            self._fp.write("\n")
            self._fp.flush()
        except OSError as exc:
            logger.warning("llm_logger write failed: %s", exc)

    def _write_raw(self, text: str) -> None:
        if not self._fp:
            return
        try:
            self._fp.write(text)
            if not text.endswith("\n"):
                self._fp.write("\n")
            self._fp.flush()
        except OSError as exc:
            logger.warning("llm_logger raw write failed: %s", exc)

    # ---------------- 公开 API ---------------- #

    def log_response_meta(
        self,
        status_code: int,
        headers: Optional[Any] = None,
    ) -> None:
        """记录响应头与状态码（headers 接受 requests 的 CaseInsensitiveDict）。"""
        try:
            headers_dict = dict(headers or {})
        except Exception:  # noqa: BLE001 - headers 可能是 Mapping/None
            headers_dict = {}
        self._write_section(
            "RESPONSE_META",
            {"status_code": status_code, "headers": headers_dict},
        )

    def log_response_body(self, body: Any) -> None:
        """非流式响应：一次性记录完整响应 body（dict 或 raw text 都行）。"""
        self._write_section("RESPONSE_BODY", body)

    def log_stream_raw_line(self, line: str) -> None:
        """流式：记录上游 SSE 的一条原始行（已去掉换行）。

        把上游真正发了什么直接落盘，便于排查「有 reasoning_content 但没 content」
        这类「前端看不到任何回复」的诡异 case。
        """
        if line is None:
            return
        self._raw_line_count += 1
        self._write_raw(f"[raw#{self._raw_line_count:04d}] {line}")

    def log_stream_delta(self, delta: str) -> None:
        """流式：记录一段被解析出来、向上 yield 的有效文本。"""
        if not delta:
            return
        self._stream_chunks.append(delta)
        self._write_raw(f"[delta] {delta!r}")

    def log_error(self, message: str) -> None:
        self._write_section("ERROR", str(message))

    def close(self, summary_extra: Optional[Dict[str, Any]] = None) -> None:
        if self._closed:
            return
        self._closed = True
        ended_at = time.time()
        final_text = "".join(self._stream_chunks)
        summary: Dict[str, Any] = {
            "ended_at": datetime.fromtimestamp(ended_at).isoformat(timespec="milliseconds"),
            "elapsed_ms": int((ended_at - self.started_at) * 1000),
            "stream_chunks_count": len(self._stream_chunks),
            "stream_raw_lines_count": self._raw_line_count,
            "final_text_length": len(final_text),
            "final_text": final_text,
        }
        if summary_extra:
            summary.update(summary_extra)
        self._write_section("SUMMARY", summary)
        if self._fp:
            try:
                self._fp.close()
            except OSError:
                pass
            self._fp = None

    # 支持 `with LLMCallLogger(...) as call_log:` 的写法。
    def __enter__(self) -> "LLMCallLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.log_error(f"{exc_type.__name__ if exc_type else 'Exception'}: {exc}")
        self.close()
