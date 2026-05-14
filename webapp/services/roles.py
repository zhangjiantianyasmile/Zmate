"""Zmate 大模型角色（system prompt）注册表。

`role` 字段在 OpenAI Chat Completions 协议里是固定枚举（system/user/assistant），
不接受 `热点提取师` 这种自定义值。所以我们把「角色」放在 `system` 消息的
**内容**里描述，对外用一个 scene 标识来切换：

    scene=default     → 默认 Zmate 人设（温暖犀利的内容洞察伙伴）
    scene=summarizer  → 内容精炼师，把长文压缩成 5 分钟口头摘要
    scene=topic_pick  → 热点提取师，从候选话题里挑出最值得关注的几条
    scene=debater     → 辩证分析师，针对争议话题列正反两派依据

调用方式：
    1. 显式：前端在 `/api/zmate/chat` 的请求体里带 `scene=summarizer`；
    2. 自动：未带 scene 时，`auto_pick_scene(user_text, has_document)`
       会根据用户最后一条消息的关键词 + 是否在文档场景下，挑一个最合适的。

注意：所有角色都仍走 `role=system`，只是内容不同；这是 Chat Completions
协议下表达「让模型扮演 X」的正确做法。
"""
from __future__ import annotations

from typing import Iterable, Optional


# ---------------- scene 枚举 ---------------- #

DEFAULT = "default"
SUMMARIZER = "summarizer"
TOPIC_PICK = "topic_pick"
DEBATER = "debater"

ALLOWED = {DEFAULT, SUMMARIZER, TOPIC_PICK, DEBATER}


# ---------------- 各角色的 system prompt ---------------- #

ROLE_PROMPTS = {
    DEFAULT: (
        "你是一个温暖、犀利、善于发掘内容洞察的 AI 知识伙伴。"
        "回答风格要：1) 中文为主，自然亲切；2) 先抛观点，再给依据；"
        "3) 善用结构化要点和短段落；4) 涉及热点时主动列出对应的事件/数据/利益相关方；"
        "5) 当用户在阅读某篇文章/回答时，结合用户提供的文档摘要内容回答；"
        "6) 对不确定的事实保持克制，主动告知用户去做交叉验证。"
    ),
    SUMMARIZER: (
        "你是一名『内容精炼师』，专门把一段长文压缩成可在 1 分钟内口头讲完的摘要。"
        "工作规范："
        "1) 先用一句不超过 40 字的总览，点出文章核心立场或答案；"
        "2) 用 3-5 个结构化要点展开，每点先抛观点再给一句依据；"
        "3) 控制全文 50~200 字之间，便于讲述者一气讲完；"
        "4) 保留原文关键数据 / 人名 / 时间，去掉重复表述与情绪化措辞；"
        "5) 若用户提供的文档信息明显不足，直说『信息不足，无法给出可靠摘要』，"
        "   不要凭空补全；"
        "6) 默认中文输出，除非用户明确要求其他语言。"
    ),
    TOPIC_PICK: (
        "你是一名『热点提取师』，擅长从给定候选话题里挑出最值得普通人关注的几条。"
        "判断标准："
        "1) 是否具有公共讨论价值，能反映行业 / 政策 / 社会的真实趋势；"
        "2) 是否对普通人有信息增量，而不是纯娱乐八卦、明星私事；"
        "3) 入选话题之间尽量覆盖多个领域，避免主题重复。"
        "输出要求："
        "1) 严格只能从给定候选中选取，不要凭空生成新话题或新 id；"
        "2) 每条话题先给标题，再用一行不超过 40 字的『为什么值得看』作为推荐理由；"
        "3) 当调用方要求 JSON 输出时，严格按要求格式输出，不要加 markdown 代码块。"
    ),
    DEBATER: (
        "你是一名『辩证分析师』，遇到争议性、对立性的话题时帮用户看清两方逻辑。"
        "工作规范："
        "1) 先用一句话客观复述用户提出的争议点，不要先站队；"
        "2) 分『支持方』『反对方』两栏，各列 2-3 条核心论据，每条配一句话出处或类比；"
        "3) 给出『分歧根源』一段：点出双方真正在争什么（价值观分歧 / 事实分歧 / "
        "   利益分歧）；"
        "4) 如果确实存在共识区域，单列『双方都同意』一段；"
        "5) 不替用户做最终判断，但鼓励用户基于上述材料形成自己的判断；"
        "6) 涉及未经证实的事实，明确标注『这一点存在争议，建议交叉验证』。"
    ),
}


def get_prompt(scene: Optional[str]) -> str:
    """根据 scene 名称返回对应的 system prompt；未知值回退到 default。"""
    key = (scene or "").strip().lower()
    return ROLE_PROMPTS.get(key, ROLE_PROMPTS[DEFAULT])


def normalize_scene(scene: Optional[str]) -> str:
    """把外部传入的 scene 归一化到合法枚举；非法值返回 default。"""
    key = (scene or "").strip().lower()
    return key if key in ALLOWED else DEFAULT


# ---------------- 自动场景识别 ---------------- #

# 关键词是「最后一条用户消息」的命中检测，按优先级匹配：
#   debater > summarizer > topic_pick > default
# 之所以让 debater 优先，是因为「这件事的争议」「应不应该」往往同时含
# 「热点」或「摘要」字眼，但本质上更想要辩证分析。
_DEBATER_KEYWORDS = (
    "争议", "辩论", "辩证", "正反", "对立", "分歧", "两派",
    "支持还是反对", "应不应该", "该不该", "对不对", "对吗",
    "是不是错", "合理吗",
)

_SUMMARIZER_KEYWORDS = (
    "摘要", "总结", "梳理", "概括", "提炼", "要点", "拎重点", "划重点",
    "5 分钟", "几分钟", "口头讲", "缩成", "讲一遍",
)

_TOPIC_PICK_KEYWORDS = (
    "热点", "热搜", "热议", "今日新闻", "今天的新闻", "今天值得",
    "值得关注", "今天有什么",
)


def _hits(text: str, keywords: Iterable[str]) -> bool:
    return any(kw in text for kw in keywords)


def auto_pick_scene(
    user_text: Optional[str] = None,
    has_document: bool = False,
) -> str:
    """根据用户最后一条消息 / 是否在文档场景，挑一个最合适的 scene。

    匹配优先级（高 → 低）：debater > summarizer > topic_pick > default。
    文档场景下偏向 summarizer：阅读时问的「这篇里」、「这段说的」之类
    短指令往往就是要摘要 / 梳理。
    """
    text = (user_text or "").strip()
    if not text:
        return SUMMARIZER if has_document else DEFAULT

    if _hits(text, _DEBATER_KEYWORDS):
        return DEBATER
    if _hits(text, _SUMMARIZER_KEYWORDS):
        return SUMMARIZER
    if _hits(text, _TOPIC_PICK_KEYWORDS):
        return TOPIC_PICK

    # 阅读文档时没命中任何关键词，但用户在『当前阅读的文档』面板里发问，
    # 默认按摘要类需求处理。
    if has_document:
        return SUMMARIZER
    return DEFAULT
