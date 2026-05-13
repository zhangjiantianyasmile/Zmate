"""知乎站内搜索调用与本地兜底。

- 数据源：知乎开放平台官方接口
  GET https://developer.zhihu.com/api/v1/content/zhihu_search
  鉴权：Authorization: Bearer <app_secret>，X-Request-Timestamp: <unix_seconds>
- 失败/无 secret 时回退到本地 mock 数据，便于离线演示。
- 单次调用最大 10 条（接口约束），上层做参数收敛。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from . import mock_data
from .config_loader import load_config


logger = logging.getLogger(__name__)


ZHIHU_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"
REQUEST_TIMEOUT = 10  # seconds
MAX_COUNT = 20


def _normalize_search_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把知乎开放平台 Item 数组裁剪成前端需要的字段。"""
    out: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        title = raw.get("Title")
        if not title:
            continue
        comments_src = raw.get("CommentInfoList") or []
        comments_norm = [
            {"content": str((c or {}).get("Content") or "").strip()}
            for c in comments_src
            if isinstance(c, dict) and (c or {}).get("Content")
        ]
        out.append(
            {
                "title": str(title),
                "content_type": str(raw.get("ContentType") or ""),
                "content_id": str(raw.get("ContentID") or ""),
                "excerpt": str(raw.get("ContentText") or ""),
                "url": str(raw.get("Url") or ""),
                "comment_count": int(raw.get("CommentCount") or 0),
                "vote_count": int(raw.get("VoteUpCount") or 0),
                "author_name": str(raw.get("AuthorName") or ""),
                "author_avatar": str(raw.get("AuthorAvatar") or ""),
                "author_badge_text": str(raw.get("AuthorBadgeText") or ""),
                "edit_time": int(raw.get("EditTime") or 0),
                "comments": comments_norm,
                "authority_level": str(raw.get("AuthorityLevel") or ""),
                "ranking_score": float(raw.get("RankingScore") or 0.0),
            }
        )
    return out


def _fetch_from_zhihu(
    app_secret: str, query: str, count: int
) -> Optional[Dict[str, Any]]:
    """调用知乎开放平台的 zhihu_search 接口。"""
    headers = {
        "Authorization": f"Bearer {app_secret}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    }
    params = {"Query": query, "Count": max(1, min(int(count), MAX_COUNT))}
    try:
        resp = requests.get(
            ZHIHU_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("zhihu_search request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "zhihu_search http %s: %s", resp.status_code, resp.text[:200]
        )
        return None

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("zhihu_search bad json: %s", exc)
        return None

    if not isinstance(body, dict):
        return None

    code = body.get("Code")
    if code != 0:
        logger.warning("zhihu_search business error: %s", body)
        return {
            "items": [],
            "search_hash_id": "",
            "has_more": False,
            "empty_reason": (
                str(body.get("Message") or "") or f"知乎搜索错误码 {code}"
            ),
        }

    data = body.get("Data") or {}
    items = _normalize_search_items(data.get("Items") or [])
    return {
        "items": items,
        "search_hash_id": str(data.get("SearchHashId") or ""),
        "has_more": bool(data.get("HasMore") or False),
        "empty_reason": str(data.get("EmptyReason") or ""),
    }


def _mock_search(query: str, count: int) -> Dict[str, Any]:
    """无 secret / 接口异常时的本地兜底。基于 mock feed 生成相关条目。"""
    seed = abs(hash(query)) & 0xFFFFFFFF
    feed = mock_data.generate_feed(seed=seed, size=max(count + 2, 8))
    items: List[Dict[str, Any]] = []
    for it in feed[:count]:
        author = it.get("author") or {}
        original_title = it.get("title") or it.get("question") or query
        items.append(
            {
                "title": f"{original_title}（与「{query}」相关）",
                "content_type": (
                    "Article" if it.get("type") == "article" else "Answer"
                ),
                "content_id": str(it.get("id") or ""),
                "excerpt": (it.get("excerpt") or "")[:200],
                "url": "",
                "comment_count": int(it.get("comment_count") or 0),
                "vote_count": int(it.get("vote_count") or 0),
                "author_name": str(author.get("name") or "知乎用户"),
                "author_avatar": "",
                "author_badge_text": str(author.get("headline") or ""),
                "edit_time": int(it.get("publish_time") or 0),
                "comments": [],
                "authority_level": "",
                "ranking_score": 0.0,
            }
        )
    return {
        "items": items,
        "search_hash_id": f"mock-{int(time.time())}",
        "has_more": False,
        "empty_reason": "" if items else "本地暂无相关内容",
    }


def search(query: str, count: int = 10) -> Dict[str, Any]:
    """对外入口：返回 {query, count, items, source, fetched_at, ...}。

    流程：
      1. query 为空直接返回空结果与提示；
      2. 有 zhihu_app_secret 时调用知乎搜索接口；
      3. 调用失败或无 secret 时回退 mock，保证前端始终能渲染。
    """
    query = (query or "").strip()
    count = max(1, min(int(count) if count else 10, MAX_COUNT))
    now_ts = int(time.time())

    if not query:
        return {
            "query": query,
            "count": count,
            "items": [],
            "search_hash_id": "",
            "has_more": False,
            "empty_reason": "请输入搜索关键词",
            "source": "empty",
            "fetched_at": now_ts,
        }

    cfg = load_config()
    app_secret = (cfg.get("zhihu_app_secret") or "").strip()

    payload: Optional[Dict[str, Any]] = None
    if app_secret:
        payload = _fetch_from_zhihu(app_secret, query, count)
    else:
        logger.info("zhihu_app_secret missing, search falls back to mock")

    if payload and payload.get("items"):
        return {
            **payload,
            "query": query,
            "count": count,
            "source": "zhihu_open_api",
            "fetched_at": now_ts,
        }

    fallback = _mock_search(query, count)
    return {
        **fallback,
        "query": query,
        "count": count,
        "source": "mock" if not app_secret else "mock_fallback",
        "fetched_at": now_ts,
        "remote_empty_reason": (payload or {}).get("empty_reason") or "",
    }
