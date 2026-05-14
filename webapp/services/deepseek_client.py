"""DeepSeek Chat 客户端封装，兼容 OpenAI Chat Completions 协议。"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Dict, Generator, Iterable, List, Optional

import requests

from . import roles
from .llm_logger import LLMCallLogger


logger = logging.getLogger(__name__)


# 「默认 Zmate 人设」统一收口到 roles 模块，本变量作为向后兼容的别名保留，
# 避免外部代码 / 历史调用一改就崩。
SYSTEM_PROMPT = roles.get_prompt(roles.DEFAULT)


# 单次 HTTP 请求超时（秒）。集中暴露成模块级常量，方便 server.py 在
# config 里未配置时回落到这里，避免「默认值」散落在多个文件。
DEFAULT_TIMEOUT = 60


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def is_ready(self) -> bool:
        return bool(self.api_key)

    def chat_once(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
    ) -> str:
        """非流式调用，返回完整 content；适合让模型一次性产出结构化结果。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        call_log = LLMCallLogger(
            provider="deepseek",
            model=self.model,
            request_url=url,
            request_headers=headers,
            request_payload=payload,
            stream=False,
        )
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            # 防御性锁定 utf-8：DeepSeek 当前响应头带 charset=utf-8 不会踩坑，
            # 但 OpenAI 兼容代理 / 反代后面接其它实现时不一定带；统一锁死避免
            # `iter_lines(decode_unicode=True)` fall back 到 Latin-1 出现中文乱码。
            resp.encoding = "utf-8"
            call_log.log_response_meta(resp.status_code, resp.headers)
            if resp.status_code >= 400:
                call_log.log_response_body(resp.text)
                call_log.log_error(f"http {resp.status_code}")
                raise RuntimeError(
                    f"deepseek http {resp.status_code}: {resp.text[:200]}"
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
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """以 SSE 流的形式产生增量 token。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        call_log = LLMCallLogger(
            provider="deepseek",
            model=self.model,
            request_url=url,
            request_headers=headers,
            request_payload=payload,
            stream=True,
        )
        try:
            with requests.post(
                url, headers=headers, json=payload, stream=True, timeout=self.timeout
            ) as resp:
                # 同 chat_once：覆盖 requests 对没 charset 的响应做 Latin-1 兜底
                # 的猜测，确保 iter_lines(decode_unicode=True) 按 utf-8 解码。
                resp.encoding = "utf-8"
                call_log.log_response_meta(resp.status_code, resp.headers)
                if resp.status_code >= 400:
                    err = resp.text[:500]
                    logger.warning("DeepSeek error %s: %s", resp.status_code, err)
                    call_log.log_response_body(err)
                    call_log.log_error(f"http {resp.status_code}")
                    fallback = f"[Zmate 调用模型出错 {resp.status_code}：{err}]"
                    call_log.log_stream_delta(fallback)
                    yield fallback
                    return
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    call_log.log_stream_raw_line(raw_line)
                    if raw_line.startswith("data: "):
                        raw_line = raw_line[len("data: "):]
                    if raw_line.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        call_log.log_stream_delta(content)
                        yield content
        except requests.RequestException as exc:
            logger.warning("deepseek request failed: %s", exc)
            call_log.log_error(f"RequestException: {exc}")
            fallback = f"\n[Zmate 调用模型失败：{exc}]"
            call_log.log_stream_delta(fallback)
            yield fallback
        finally:
            call_log.close()


# ---------------- Mock fallback ---------------- #

_HOT_TOPICS_TEMPLATES = [
    "📌 国产大模型阵营今天又有重要进展，{tag} 表现尤其亮眼。",
    "🔥 {tag} 相关话题在过去 24 小时占据了热榜前三，讨论核心是「价值再分配」。",
    "🧠 关于 {tag}，有三种立场你最好都听一遍，再去判断你支持哪一种。",
    "📰 今天值得追的故事：{tag} 引起了一波连锁反应，背后有更深的产业逻辑。",
]

_DEFAULT_TAGS = [
    "AI Agent 落地",
    "新能源出口",
    "宏观经济政策",
    "AI 编程工具",
    "影视行业",
    "中产理财焦虑",
    "心理健康话题",
    "短剧出海",
]


def mock_chat_stream(
    messages: List[Dict[str, str]],
    document: Optional[Dict[str, str]] = None,
) -> Iterable[str]:
    """没有 DeepSeek 密钥时的本地规则化流式回复。"""
    last_user = next(
        (m for m in reversed(messages) if m.get("role") == "user"), None
    )
    user_text = (last_user or {}).get("content", "").strip()

    if document:
        text = (
            f"我注意到你正在阅读《{document.get('title','这篇内容')}》，作者是 "
            f"{document.get('author','一位创作者')}。\n\n"
            "针对你的提问，我有几点可以聊：\n\n"
            "1) 文章的核心论点比较强调实践经验，不是单纯的理论复述；\n"
            "2) 如果想验证里面的结论，可以先关注作者列举的几条数据来源；\n"
            "3) 你也可以问我「帮我提炼一份 5 分钟的口头摘要」或「这篇里最有争议的观点是什么」。\n\n"
            f"你的提问：「{user_text}」。我的快速回应是——这个问题里其实藏了三层：现象、机制、应对。"
            "我们可以挑一个先聊。"
        )
    elif any(kw in user_text for kw in ["热点", "新闻", "今天", "推荐"]):
        chosen = random.sample(_DEFAULT_TAGS, k=4)
        bullets = "\n".join(
            random.choice(_HOT_TOPICS_TEMPLATES).format(tag=t) for t in chosen
        )
        text = (
            "今天我帮你拉了几条值得关注的热点：\n\n"
            f"{bullets}\n\n"
            "想从哪一条聊起？告诉我「第 1 条」或者直接打主题词都行。"
        )
    elif user_text:
        text = (
            f"你说的「{user_text}」，我的第一反应是——\n\n"
            "先把它拆解成「现象 / 原因 / 影响」三个层面会更清楚。\n"
            "1) 现象层：这件事在公开信息里通常被怎样描述？\n"
            "2) 原因层：背后有哪些利益相关方在推动？\n"
            "3) 影响层：对像我们这样的普通从业者意味着什么？\n\n"
            "（提示：当前为本地模拟回复，配置 DeepSeek 密钥后可获得更深入的回答。）"
        )
    else:
        text = (
            "嗨，我是 Zmate👋\n\n"
            "你可以问我：\n• 今天有什么值得关注的热点？\n• 帮我拆解一下这篇文章\n• 我想读一篇关于 XX 的内容，你推荐什么？"
        )

    for chunk in _stream_text(text):
        yield chunk


def _stream_text(text: str) -> Iterable[str]:
    buffer = ""
    for ch in text:
        buffer += ch
        if len(buffer) >= 4 or ch in "，。！？\n":
            yield buffer
            buffer = ""
            time.sleep(0.025)
    if buffer:
        yield buffer
