"""知乎热榜数据获取与本地缓存。

- 数据源：知乎开放平台官方接口
  GET https://developer.zhihu.com/api/v1/content/hot_list
  鉴权：Authorization: Bearer <app_secret>，X-Request-Timestamp: <unix_seconds>
- 缓存策略：
  - 进程启动后第一次请求会优先读取磁盘缓存（webapp/cache/hot_list.json）
  - 缓存有效期 1 小时，未过期直接返回；过期或不存在则请求远端接口并回写缓存
  - 远端调用失败时优先回退到磁盘已有缓存（即使过期），再不可用才回退 mock
  - `cache_only=True` 仅返回缓存（有则返回、即使过期；无则 mock），不发任何远端请求；
    上游链路（如 hot_picks）需要 Top 20 候选但不愿因热点选拔触发知乎接口刷新时使用。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import mock_data
from .config_loader import load_config


logger = logging.getLogger(__name__)


ZHIHU_HOT_LIST_URL = "https://developer.zhihu.com/api/v1/content/hot_list"
CACHE_TTL_SECONDS = 1 * 60 * 60  # 1 小时
REQUEST_TIMEOUT = 6  # seconds

WEBAPP_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = WEBAPP_DIR / "cache"
CACHE_FILE = CACHE_DIR / "hot_list.json"


_CACHE_LOCK = threading.Lock()
# 进程内内存缓存：避免每次请求都读磁盘
_MEMORY_CACHE: Dict[str, Any] = {"items": None, "fetched_at": 0.0, "source": None}
# 是否已尝试过加载磁盘缓存
_DISK_LOADED = False


def _load_disk_cache() -> Optional[Dict[str, Any]]:
    if not CACHE_FILE.exists():
        return None
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("hot_list cache file unreadable: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return None
    return {
        "items": items,
        "fetched_at": float(data.get("fetched_at") or 0.0),
        "source": data.get("source") or "disk-cache",
    }


def _save_disk_cache(items: List[Dict[str, Any]], fetched_at: float, source: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": items,
            "fetched_at": fetched_at,
            "source": source,
        }
        tmp = CACHE_FILE.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        tmp.replace(CACHE_FILE)
    except OSError as exc:
        logger.warning("hot_list cache file write failed: %s", exc)


def _ensure_disk_loaded() -> None:
    """首次访问时把磁盘缓存读进内存。"""
    global _DISK_LOADED
    if _DISK_LOADED:
        return
    disk = _load_disk_cache()
    if disk:
        _MEMORY_CACHE.update(disk)
        logger.info(
            "hot_list cache loaded from disk: %d items, age=%.1fh, source=%s",
            len(disk["items"]),
            max(0.0, (time.time() - disk["fetched_at"]) / 3600.0),
            disk["source"],
        )
    _DISK_LOADED = True


def _normalize_zhihu_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把知乎开放平台 Item 数组标准化成前端使用的结构。"""
    out: List[Dict[str, Any]] = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        title = raw.get("Title") or raw.get("title")
        if not title:
            continue
        url = raw.get("Url") or raw.get("url") or ""
        thumbnail = raw.get("ThumbnailUrl") or raw.get("thumbnail_url") or ""
        summary = raw.get("Summary") or raw.get("summary") or ""
        out.append(
            {
                "rank": idx + 1,
                "title": str(title)[:80],
                "metric": "热议中",
                "excerpt": str(summary)[:160],
                "url": url,
                "thumbnail": thumbnail,
                "is_hot": idx < 3,
                "id": f"zhihu-{idx}-{abs(hash(url or title)) % 10**8}",
            }
        )
    return out


def _fetch_from_zhihu(app_secret: str, limit: int = 30) -> Optional[List[Dict[str, Any]]]:
    """调用知乎开放平台的 hot_list 接口。"""
    headers = {
        "Authorization": f"Bearer {app_secret}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    }
    # 接口约定 Limit 上限 30
    params = {"Limit": max(1, min(int(limit), 30))}
    try:
        resp = requests.get(
            ZHIHU_HOT_LIST_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("zhihu hot_list request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "zhihu hot_list http %s: %s", resp.status_code, resp.text[:200]
        )
        return None

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("zhihu hot_list bad json: %s", exc)
        return None

    code = body.get("Code") if isinstance(body, dict) else None
    if code != 0:
        logger.warning("zhihu hot_list business error: %s", body)
        return None

    raw_items = (body.get("Data") or {}).get("Items") or []
    if not isinstance(raw_items, list) or not raw_items:
        logger.warning("zhihu hot_list empty items")
        return None

    items = _normalize_zhihu_items(raw_items)
    return items or None


def _paginate(items: List[Dict[str, Any]], page: int, size: int) -> Dict[str, Any]:
    """按 page/size 做环形切片，超出尾部时回绕到头部，保持每页 size 条。"""
    total = len(items)
    if total == 0 or size <= 0:
        return {
            "items": [],
            "page": 0,
            "page_size": size,
            "total": total,
            "total_pages": 0,
        }

    effective_size = min(size, total)
    total_pages = max(1, (total + effective_size - 1) // effective_size)
    norm_page = page % total_pages if total_pages > 0 else 0
    start = (norm_page * effective_size) % total
    end = start + effective_size
    if end <= total:
        page_items = items[start:end]
    else:
        page_items = items[start:] + items[: end - total]

    return {
        "items": page_items,
        "page": norm_page,
        "page_size": effective_size,
        "total": total,
        "total_pages": total_pages,
    }


def fetch_hot_list(
    size: int = 10,
    force_refresh: bool = False,
    page: int = 0,
    cache_only: bool = False,
) -> Dict[str, Any]:
    """返回 {items, source, fetched_at, page, page_size, total, total_pages, cache}。

    流程：内存缓存 -> 磁盘缓存（首次启动） -> 知乎开放接口 -> 旧缓存 -> mock
    - 缓存命中条件：fetched_at 距今不超过 CACHE_TTL_SECONDS
    - 切片由 page/size 控制，超出末尾会环回到头部，保证翻页可以一直循环
    - `cache_only=True` 时跳过远端调用：有缓存就返回（即使过期，标记为 stale），
      没缓存就 mock 兜底；和 `force_refresh` 互斥（cache_only 优先生效）。
    """
    with _CACHE_LOCK:
        _ensure_disk_loaded()

        now = time.time()
        cached_items = _MEMORY_CACHE.get("items")
        cached_ts = _MEMORY_CACHE.get("fetched_at") or 0.0
        cache_fresh = bool(cached_items) and (now - cached_ts < CACHE_TTL_SECONDS)

        if cache_fresh and not force_refresh:
            page_info = _paginate(cached_items, page, size)
            return {
                **page_info,
                "source": _MEMORY_CACHE.get("source") or "cache",
                "fetched_at": cached_ts,
                "cache": "hit",
            }

        if cache_only:
            # 上游显式要求「不联网」：有什么用什么，不发请求也不刷缓存。
            if cached_items:
                page_info = _paginate(cached_items, page, size)
                return {
                    **page_info,
                    "source": _MEMORY_CACHE.get("source") or "cache",
                    "fetched_at": cached_ts,
                    "cache": "hit" if cache_fresh else "stale",
                }
            mock_items = mock_data.generate_hot_list(size=max(size * 2, 12))
            page_info = _paginate(mock_items, page, size)
            return {
                **page_info,
                "source": "mock",
                "fetched_at": now,
                "cache": "miss",
            }

        cfg = load_config()
        app_secret = (cfg.get("zhihu_app_secret") or "").strip()

        if app_secret:
            # 一次性把官方允许的最多条目拉回来，前端再按 page/size 翻页
            items = _fetch_from_zhihu(app_secret, limit=30)
            if items:
                source = "zhihu_open_api"
                _MEMORY_CACHE.update(
                    {"items": items, "fetched_at": now, "source": source}
                )
                _save_disk_cache(items, now, source)
                page_info = _paginate(items, page, size)
                return {
                    **page_info,
                    "source": source,
                    "fetched_at": now,
                    "cache": "refresh",
                }
        else:
            logger.info("zhihu_app_secret missing, skip remote fetch")

        # 远端失败：优先返回旧的（可能已过期）缓存
        if cached_items:
            logger.info("hot_list remote unavailable, fall back to stale cache")
            page_info = _paginate(cached_items, page, size)
            return {
                **page_info,
                "source": _MEMORY_CACHE.get("source") or "stale-cache",
                "fetched_at": cached_ts,
                "cache": "stale",
            }

        # 完全没有数据：mock 兜底（生成两倍数据方便循环翻页）
        mock_items = mock_data.generate_hot_list(size=max(size * 2, 12))
        page_info = _paginate(mock_items, page, size)
        return {
            **page_info,
            "source": "mock",
            "fetched_at": now,
            "cache": "miss",
        }
