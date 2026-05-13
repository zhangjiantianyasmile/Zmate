"""DeepSeek Chat 客户端封装，兼容 OpenAI Chat Completions 协议。"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Dict, Generator, Iterable, List, Optional

import requests


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "你是 Zmate（知乎 Mate），一个温暖、犀利、善于发掘内容洞察的 AI 知识伙伴。"
    "回答风格要：1) 中文为主，自然亲切；2) 先抛观点，再给依据；"
    "3) 善用结构化要点和短段落；4) 涉及热点时主动列出对应的事件/数据/利益相关方；"
    "5) 当用户在阅读某篇文章/回答时，结合用户提供的文档摘要内容回答；"
    "6) 对不确定的事实保持克制，主动告知用户去做交叉验证。"
)


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: int = 60,
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
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"deepseek http {resp.status_code}: {resp.text[:200]}"
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
        with requests.post(
            url, headers=headers, json=payload, stream=True, timeout=self.timeout
        ) as resp:
            if resp.status_code >= 400:
                err = resp.text[:500]
                logger.warning("DeepSeek error %s: %s", resp.status_code, err)
                yield f"[Zmate 调用模型出错 {resp.status_code}：{err}]"
                return
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
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
                    yield content


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
