"""生成知乎风格的 feed / 详情 / 评论的 mock 数据。

数据并非来自知乎，仅用于本地体验。所有文案为本项目原创。
"""
from __future__ import annotations

import hashlib
import random
import time
from typing import Any, Dict, List, Optional


AUTHORS = [
    {"name": "陆离十一", "headline": "AI 产品经理 / 关注大模型应用落地", "avatar_color": "#5B8DEF"},
    {"name": "夜航船", "headline": "工程师，写过几本枕边技术书", "avatar_color": "#F4A261"},
    {"name": "山间月", "headline": "心理咨询师 / 临床心理学博士", "avatar_color": "#7FB069"},
    {"name": "白鹿原上的风", "headline": "金融分析师，关注宏观经济", "avatar_color": "#E76F51"},
    {"name": "竹影清风", "headline": "前端工程师 / 写作爱好者", "avatar_color": "#264653"},
    {"name": "Kris 的夜读", "headline": "终身学习者，分享读书与思考", "avatar_color": "#9D4EDD"},
    {"name": "三里屯小张", "headline": "广告创意 / 看人间百态", "avatar_color": "#F25C54"},
    {"name": "栖梧", "headline": "中医博士在读", "avatar_color": "#06A77D"},
    {"name": "理想温度", "headline": "新能源行业从业者", "avatar_color": "#3A86FF"},
    {"name": "晚来天欲雪", "headline": "古典文学爱好者", "avatar_color": "#8338EC"},
    {"name": "碳基观察员", "headline": "数据分析师 / 喜欢做图表", "avatar_color": "#FB8500"},
    {"name": "Lily 的厨房", "headline": "美食博主 / 家庭烘焙", "avatar_color": "#EF476F"},
]

QUESTION_TEMPLATES = [
    "如何看待 {topic}？",
    "{topic} 真的像大家说的那样重要吗？",
    "为什么越来越多的人开始关注 {topic}？",
    "对于 {topic}，普通人应该如何应对？",
    "{topic} 的未来会走向哪里？",
    "在 {topic} 面前，年轻人最容易踩的坑是什么？",
    "30 岁了还能从零开始学 {topic} 吗？",
    "如何用一句话向小朋友解释 {topic}？",
    "{topic} 是不是被高估了？",
    "{topic} 背后有哪些不为人知的细节？",
]

TOPICS = [
    "大语言模型在企业内部的落地实践",
    "中国新能源汽车出海",
    "通胀与低利率并存的宏观环境",
    "AI 编程助手对软件工程师的影响",
    "心理咨询行业的供需失衡",
    "短剧行业的高速增长",
    "Python 在数据科学中的不可替代性",
    "城市更新与年轻人租房选择",
    "围炉煮茶式的慢生活",
    "深度睡眠对认知能力的影响",
    "上海到杭州的两小时通勤族",
    "社区咖啡馆的生意模型",
    "GPT-5 与 Gemini 之争",
    "中医药现代化的边界",
    "未来十年最值得加入的赛道",
    "Vision Pro 在国内的落地",
    "宫崎骏新片《你想活出怎样的人生》",
    "城市中产的资产配置焦虑",
    "Cursor、Claude Code 等 AI IDE 的体验",
    "县城婚礼里的人情账",
]

ANSWER_OPENERS = [
    "先说结论：",
    "在这个行业摸爬滚打了八年，让我用一段亲历来回答这个问题。",
    "看到这个问题，下意识地想起去年在一个咖啡馆里和老朋友的争论。",
    "谢邀。这个话题其实可以从三个层面来谈：",
    "聊这个之前，先放一组我抓的数据：",
    "我先抛个反共识的观点：",
    "之前有同行问过我类似的问题，我当时的回答现在回过头来看依然适用。",
]

ANSWER_PARAGRAPHS = [
    "市场永远不缺少叙事，缺的是能把叙事变成现金流的人。我们调研过六十多家初创公司，能在一年内把毛利做到 35% 以上的，几乎都做对了同一件事：把通用能力包装成行业纵深的工作流，而不是停留在 demo 层面。",
    "对于大多数普通人来说，与其追逐风口，不如先盘点自己手头能复用的资产：可迁移的能力、稳定的人脉、能够长期积累的内容。这三件事会在五年后帮你穿越周期。",
    "不要被那些「速成攻略」骗了。任何看起来轻松的捷径，背后都有一份你看不到的报价单。真正能让你和别人拉开差距的，恰恰是那些没有热度、没人愿意做的笨功夫。",
    "技术不是答案，业务才是。再强的工具，也要落到一个具体的场景里才能产生价值。这听起来像废话，但很多团队都死在了「我们做了一个特别牛的能力，但不知道卖给谁」上。",
    "对个体来说，我反复强调一句话：「先把今天活成一个值得别人复制的样本，再考虑去复制别人的成功」。这听上去玄学，但其实是最朴素的方法论。",
    "数据从来不会替你做决策，它只会让你更快地否定一些错误选项。我每次做长决策的时候都会先列三件「绝对不会做的事」，剩下的反而豁然开朗。",
]

IMAGE_POOL = [
    "https://picsum.photos/seed/zmate-{seed}/720/420",
    "https://picsum.photos/seed/zmate-img-{seed}/640/360",
]

HOT_FALLBACK = [
    {"title": "国产新能源车出口连续 12 个月正增长", "metric": "1280 万热度"},
    {"title": "央行宣布定向降准 0.25 个百分点", "metric": "1124 万热度"},
    {"title": "OpenAI 推出 Agent Builder 引发开发者社区讨论", "metric": "987 万热度"},
    {"title": "深圳人才房政策再度调整", "metric": "823 万热度"},
    {"title": "宫崎骏新动画票房破 8 亿", "metric": "754 万热度"},
    {"title": "县城养老产业站上风口", "metric": "612 万热度"},
    {"title": "考研报名人数四年来首次下降", "metric": "534 万热度"},
    {"title": "Vision Pro 国行版正式开售", "metric": "489 万热度"},
    {"title": "短剧出海北美市场月入千万美金", "metric": "421 万热度"},
    {"title": "国产 GPU 性能逼近 H100", "metric": "385 万热度"},
    {"title": "00 后整顿婚礼习俗", "metric": "342 万热度"},
    {"title": "AI 客服替代率突破 60%", "metric": "298 万热度"},
]

CARD_TYPES = ["answer", "answer", "answer", "article", "answer", "video", "pin"]


def _make_id(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:14]


def _author(rng: random.Random) -> Dict[str, Any]:
    base = rng.choice(AUTHORS)
    initial = base["name"][0]
    return {**base, "initial": initial}


def _question(rng: random.Random) -> str:
    return rng.choice(QUESTION_TEMPLATES).format(topic=rng.choice(TOPICS))


def _excerpt(rng: random.Random, paragraphs: int = 2) -> str:
    chosen = rng.sample(ANSWER_PARAGRAPHS, k=min(paragraphs, len(ANSWER_PARAGRAPHS)))
    return "\n\n".join(chosen)


def _full_content(rng: random.Random) -> List[str]:
    opener = rng.choice(ANSWER_OPENERS)
    body = rng.sample(ANSWER_PARAGRAPHS, k=min(4, len(ANSWER_PARAGRAPHS)))
    paragraphs = [opener + body[0]] + body[1:]
    return paragraphs


def _images(rng: random.Random, count: int) -> List[str]:
    if count <= 0:
        return []
    return [
        rng.choice(IMAGE_POOL).format(seed=_make_id(time.time(), rng.random(), i))
        for i in range(count)
    ]


def _human_count(value: int) -> str:
    if value >= 10000:
        return f"{value/10000:.1f} 万"
    return str(value)


def generate_feed(seed: Optional[int] = None, size: int = 12) -> List[Dict[str, Any]]:
    """生成一组首页推荐 feed，每次刷新随机变化。"""
    rng = random.Random(seed if seed is not None else time.time_ns())
    items: List[Dict[str, Any]] = []
    for idx in range(size):
        kind = rng.choice(CARD_TYPES)
        author = _author(rng)
        question = _question(rng)
        title = question if kind == "answer" else f"{rng.choice(TOPICS)}".strip()
        if kind == "article":
            title = f"《{rng.choice(TOPICS)}》｜深度长文"
        elif kind == "pin":
            title = ""
        elif kind == "video":
            title = f"【视频】{rng.choice(TOPICS)}"

        excerpt = _excerpt(rng, paragraphs=1 if kind in ("pin", "video") else 2)
        item_id = _make_id(idx, rng.random(), title)
        votes = rng.randint(120, 38000)
        comments = rng.randint(8, 1200)
        images = _images(rng, count=rng.choice([0, 0, 1, 1, 3]))

        items.append(
            {
                "id": item_id,
                "type": kind,
                "title": title,
                "question": question if kind == "answer" else None,
                "excerpt": excerpt,
                "author": author,
                "vote_count": votes,
                "vote_count_text": _human_count(votes),
                "comment_count": comments,
                "comment_count_text": _human_count(comments),
                "images": images,
                "tag": rng.choice(["科技", "经济", "生活", "心理", "AI", "教育", "电影", "游戏"]),
                "publish_time": int(time.time()) - rng.randint(60, 60 * 60 * 36),
                "is_video": kind == "video",
            }
        )
    return items


def generate_hot_list(seed: Optional[int] = None, size: int = 12) -> List[Dict[str, Any]]:
    """无网络可用时回退使用的热榜 mock。"""
    rng = random.Random(seed if seed is not None else time.time_ns())
    base = HOT_FALLBACK.copy()
    rng.shuffle(base)
    base = base[:size]
    return [
        {
            "rank": idx + 1,
            "title": item["title"],
            "metric": item["metric"],
            "id": _make_id("hot", idx, item["title"]),
            "url": "",
            "is_hot": idx < 3,
        }
        for idx, item in enumerate(base)
    ]


def generate_categories() -> List[Dict[str, str]]:
    return [
        {"name": "推荐", "key": "recommend"},
        {"name": "关注", "key": "following"},
        {"name": "热榜", "key": "hot"},
        {"name": "视频", "key": "video"},
        {"name": "想法", "key": "pin"},
    ]


def generate_left_filters() -> List[Dict[str, str]]:
    return [
        {"name": "全部", "key": "all"},
        {"name": "AI 与大模型", "key": "ai"},
        {"name": "经济观察", "key": "economy"},
        {"name": "数码与硬件", "key": "tech"},
        {"name": "心理与个人成长", "key": "psychology"},
        {"name": "影视与娱乐", "key": "entertainment"},
        {"name": "职场与求职", "key": "career"},
        {"name": "教育与学习", "key": "education"},
    ]


def generate_promotions() -> List[Dict[str, Any]]:
    return [
        {
            "title": "Zmate Pro 体验",
            "description": "AI 助手深度集成你的阅读流，订阅后可获取专属热点早报。",
            "tag": "推广",
        },
        {
            "title": "知乎黑客松脑洞补给站",
            "description": "和上百位 Agent 一起在圈子里玩耍，碰撞硅基灵感。",
            "tag": "活动",
        },
    ]


def generate_document_detail(doc_id: str) -> Dict[str, Any]:
    """根据 id 稳定生成一篇文章/答案的详情页。"""
    seed = int(hashlib.md5(doc_id.encode("utf-8")).hexdigest(), 16) % (10**8)
    rng = random.Random(seed)
    author = _author(rng)
    question = _question(rng)
    paragraphs = _full_content(rng)
    images = _images(rng, count=rng.choice([0, 1, 2]))
    votes = rng.randint(2_000, 86_000)
    favorites = rng.randint(800, 32_000)
    comments_count = rng.randint(40, 2_400)

    related = []
    for _ in range(4):
        related.append(
            {
                "id": _make_id("rel", rng.random(), question),
                "title": _question(rng),
                "excerpt": _excerpt(rng, paragraphs=1)[:80] + "…",
            }
        )

    comments = []
    for i in range(8):
        ca = _author(rng)
        comments.append(
            {
                "id": _make_id("cmt", doc_id, i),
                "author": ca,
                "content": rng.choice(ANSWER_PARAGRAPHS)[:140] + "…",
                "like_count": rng.randint(1, 1200),
                "publish_time": int(time.time()) - rng.randint(60, 60 * 60 * 24 * 7),
                "replies": rng.randint(0, 32),
            }
        )

    return {
        "id": doc_id,
        "type": "answer",
        "title": question,
        "question": question,
        "question_id": _make_id("q", doc_id),
        "follower_count": rng.randint(800, 24_000),
        "view_count": rng.randint(20_000, 1_800_000),
        "publish_time": int(time.time()) - rng.randint(3600, 3600 * 24 * 30),
        "author": author,
        "paragraphs": paragraphs,
        "images": images,
        "vote_count": votes,
        "vote_count_text": _human_count(votes),
        "favorite_count": favorites,
        "favorite_count_text": _human_count(favorites),
        "comment_count": comments_count,
        "comment_count_text": _human_count(comments_count),
        "tags": rng.sample(
            ["AI", "大模型", "心理学", "经济", "职场", "成长", "效率", "财经", "科技", "教育"],
            k=3,
        ),
        "related_questions": related,
        "comments": comments,
    }
