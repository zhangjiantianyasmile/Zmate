(function () {
  const { getJSON, escapeHtml, formatRelativeTime, showToast } = window.ZmateAPI;

  const KIND_LABELS = {
    article: "文章",
    video: "视频",
    pin: "想法",
    answer: "回答",
  };

  const HOT_PAGE_SIZE = 10;

  const state = {
    activeTab: "recommend",
    activeFilter: "all",
    feedItems: [],
    hotPage: 0,
    hotTotalPages: 1,
    inSearch: false,
    searchQuery: "",
  };

  const els = {};

  document.addEventListener("DOMContentLoaded", () => {
    els.tabs = document.querySelector("[data-tabs]");
    els.feed = document.querySelector("[data-feed]");
    els.left = document.querySelector("[data-left-rail]");
    els.right = document.querySelector("[data-right-rail]");
    els.refresh = document.querySelector("[data-refresh-feed]");
    els.refreshStrip = document.querySelector("[data-refresh-strip]");
    els.searchInput = document.querySelector("[data-search-input]");

    document.querySelector("[data-search-form]").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = els.searchInput.value.trim();
      runSearch(q);
    });

    new window.Zmate({});

    bindRefresh();
    boot();
  });

  function bindRefresh() {
    els.refresh.addEventListener("click", () => refreshFeed({ scrollTop: true }));
    let lastTouchY = 0;
    document.addEventListener("touchstart", (e) => {
      if (window.scrollY <= 0) lastTouchY = e.touches[0].clientY;
    }, { passive: true });
    document.addEventListener("touchend", (e) => {
      const endY = (e.changedTouches[0] || {}).clientY || 0;
      if (window.scrollY <= 0 && endY - lastTouchY > 80) refreshFeed({ scrollTop: false });
      lastTouchY = 0;
    }, { passive: true });
  }

  async function boot() {
    showSkeleton();
    const tasks = [loadFeed({ initial: true }), loadHot({ initial: true })];
    await Promise.all(tasks);

    const params = new URLSearchParams(location.search);
    const q = (params.get("q") || "").trim();
    if (q) {
      if (els.searchInput) els.searchInput.value = q;
      runSearch(q, { fromUrl: true });
    }
  }

  async function runSearch(query, opts) {
    opts = opts || {};
    const q = (query || "").trim();
    if (!q) {
      showToast("请输入搜索关键词");
      return;
    }
    state.inSearch = true;
    state.searchQuery = q;

    if (!opts.fromUrl) {
      const url = new URL(location.href);
      url.searchParams.set("q", q);
      history.pushState({ q }, "", url.toString());
    }

    showSearchSkeleton(q);
    window.scrollTo({ top: 0, behavior: "smooth" });

    try {
      const params = new URLSearchParams();
      params.set("q", q);
      params.set("count", "10");
      const data = await getJSON(`/api/search?${params.toString()}`);
      renderSearchResults(data);
    } catch (err) {
      els.feed.innerHTML = `
        <div class="search-header">
          <div class="search-header__title">「${escapeHtml(q)}」的搜索结果</div>
          <button class="search-header__back" data-search-back>← 返回推荐</button>
        </div>
        <div class="feed-empty">搜索失败：${escapeHtml(err.message)}</div>`;
      bindSearchBack();
    }
  }

  function exitSearch() {
    state.inSearch = false;
    state.searchQuery = "";
    if (els.searchInput) els.searchInput.value = "";
    const url = new URL(location.href);
    url.searchParams.delete("q");
    history.pushState({}, "", url.toString());
    renderFeed(state.feedItems, true);
  }

  function bindSearchBack() {
    els.feed.querySelectorAll("[data-search-back]").forEach((btn) => {
      btn.addEventListener("click", exitSearch);
    });
  }

  async function loadFeed(opts) {
    try {
      const data = await getJSON(`/api/feed?seed=${Date.now()}`);
      state.feedItems = data.items || [];
      renderTabs(data.categories || []);
      renderLeft(data.filters || [], data.promotions || []);
      renderFeed(state.feedItems, !opts || !opts.initial);
    } catch (err) {
      els.feed.innerHTML = `<div class="feed-empty">内容加载失败：${escapeHtml(err.message)}</div>`;
    }
  }

  async function loadHot(opts) {
    opts = opts || {};
    try {
      if (!opts.initial) {
        els.right.querySelector("[data-hot-refresh]")?.classList.add("is-loading");
      }
      const params = new URLSearchParams();
      params.set("size", String(HOT_PAGE_SIZE));
      params.set("page", String(state.hotPage));
      if (opts.refresh) params.set("refresh", "1");
      const data = await getJSON(`/api/hot?${params.toString()}`);
      if (typeof data.total_pages === "number" && data.total_pages > 0) {
        state.hotTotalPages = data.total_pages;
      }
      if (typeof data.page === "number") {
        state.hotPage = data.page;
      }
      renderHot(data);
    } catch (err) {
      console.warn("hot fetch failed", err);
    } finally {
      els.right.querySelector("[data-hot-refresh]")?.classList.remove("is-loading");
    }
  }

  function nextHotPage() {
    const pages = Math.max(1, state.hotTotalPages || 1);
    state.hotPage = (state.hotPage + 1) % pages;
    loadHot({ refresh: false });
  }

  async function refreshFeed(opts) {
    if (els.refresh.classList.contains("is-loading")) return;
    els.refresh.classList.add("is-loading");
    showRefreshStrip("正在为你抓取新的内容...");
    if (opts && opts.scrollTop) window.scrollTo({ top: 0, behavior: "smooth" });

    showSkeleton();
    try {
      await loadFeed({ initial: false });
      showRefreshStrip("已为你更新 " + state.feedItems.length + " 条内容", true);
    } finally {
      els.refresh.classList.remove("is-loading");
      setTimeout(() => hideRefreshStrip(), 1600);
    }
  }

  function showRefreshStrip(text) {
    els.refreshStrip.textContent = text;
    els.refreshStrip.classList.add("is-visible");
  }

  function hideRefreshStrip() {
    els.refreshStrip.classList.remove("is-visible");
  }

  function renderTabs(categories) {
    if (!categories.length) return;
    if (els.tabs.dataset.rendered === "1") return;
    els.tabs.dataset.rendered = "1";
    els.tabs.innerHTML =
      categories
        .map(
          (c) => `<div class="tabs__item ${c.key === state.activeTab ? "is-active" : ""}" data-tab="${c.key}">${escapeHtml(c.name)}</div>`
        )
        .join("") +
      `<button class="tabs__refresh" data-refresh-feed>
         <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M17.65 6.35A7.95 7.95 0 0012 4a8 8 0 108 8h-2a6 6 0 11-1.76-4.24L13 11h7V4l-2.35 2.35z"/></svg>
         换一批
       </button>`;
    els.refresh = els.tabs.querySelector("[data-refresh-feed]");
    els.refresh.addEventListener("click", () => refreshFeed({ scrollTop: true }));

    els.tabs.querySelectorAll(".tabs__item").forEach((tabEl) => {
      tabEl.addEventListener("click", () => {
        els.tabs.querySelectorAll(".tabs__item").forEach((t) => t.classList.remove("is-active"));
        tabEl.classList.add("is-active");
        state.activeTab = tabEl.dataset.tab;
        refreshFeed({ scrollTop: true });
      });
    });
  }

  function renderLeft(filters, promotions) {
    if (!els.left) return;
    if (els.left.dataset.rendered === "1") return;
    els.left.dataset.rendered = "1";

    const filterHtml = `
      <div class="left-rail">
        <div class="left-rail__title">发现</div>
        <ul class="left-rail__list">
          ${filters
            .map(
              (f) => `<li class="left-rail__item ${f.key === state.activeFilter ? "is-active" : ""}" data-filter="${f.key}">${escapeHtml(f.name)}</li>`
            )
            .join("")}
        </ul>
      </div>
    `;

    const promoHtml = promotions
      .map(
        (p) => `
        <div class="card promo-card">
          <span class="tag">${escapeHtml(p.tag)}</span>
          <h4>${escapeHtml(p.title)}</h4>
          <p>${escapeHtml(p.description)}</p>
        </div>`
      )
      .join("");

    els.left.innerHTML = filterHtml + promoHtml;

    els.left.querySelectorAll(".left-rail__item").forEach((item) => {
      item.addEventListener("click", () => {
        els.left.querySelectorAll(".left-rail__item").forEach((i) => i.classList.remove("is-active"));
        item.classList.add("is-active");
        state.activeFilter = item.dataset.filter;
        showToast("已切换到：" + item.textContent.trim());
        refreshFeed({});
      });
    });
  }

  function renderFeed(items, animate) {
    if (!items.length) {
      els.feed.innerHTML = `<div class="feed-empty">暂时没有内容，换一批试试</div>`;
      return;
    }
    els.feed.innerHTML = items.map((item, idx) => renderFeedCard(item, idx, animate)).join("");
    els.feed.querySelectorAll("[data-doc-id]").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        const id = card.dataset.docId;
        location.href = `./detail.html?id=${encodeURIComponent(id)}`;
      });
    });
  }

  function renderFeedCard(item, idx, animate) {
    const author = item.author || {};
    const titleNode =
      item.type === "pin"
        ? ""
        : `<h3 class="feed-card__title"><a href="./detail.html?id=${encodeURIComponent(item.id)}">${escapeHtml(item.title)}</a></h3>`;
    const imagesCount = (item.images || []).length;
    const imagesHtml = imagesCount
      ? `<div class="feed-card__images count-${imagesCount === 3 ? 3 : 1}">${item.images
          .slice(0, imagesCount === 3 ? 3 : 1)
          .map((src) => `<img src="${escapeHtml(src)}" alt="" loading="lazy" />`)
          .join("")}</div>`
      : "";

    const tagPill = item.tag ? `<span class="feed-card__tag">${escapeHtml(item.tag)}</span>` : "";
    const author_avatar = `<span class="feed-card__avatar" style="background:${author.avatar_color || "#5b8def"}">${escapeHtml((author.initial || "Z"))}</span>`;

    const kindLabel = KIND_LABELS[item.type] || "推荐";

    return `
      <article class="feed-card kind-${item.type} ${animate ? "fade-in-up" : ""}" data-doc-id="${item.id}" style="animation-delay:${Math.min(idx * 30, 360)}ms">
        <div class="feed-card__topbar">
          ${author_avatar}
          <span class="feed-card__author">${escapeHtml(author.name || "知乎用户")}</span>
          <span class="feed-card__author-line">· ${escapeHtml(author.headline || "")}</span>
          ${tagPill}
        </div>
        ${titleNode}
        <p class="feed-card__excerpt">${escapeHtml(item.excerpt || "")}</p>
        ${imagesHtml}
        <div class="feed-card__meta">
          <span class="feed-card__meta-item is-vote">▲ ${escapeHtml(item.vote_count_text)} 赞同</span>
          <span class="feed-card__meta-item">💬 ${escapeHtml(item.comment_count_text)} 评论</span>
          <span class="feed-card__meta-item">🕘 ${escapeHtml(formatRelativeTime(item.publish_time))}</span>
          <span class="feed-card__meta-item" style="margin-left:auto">${escapeHtml(kindLabel)}</span>
          <button class="feed-card__cta" type="button">▼ 不感兴趣</button>
        </div>
      </article>
    `;
  }

  function showSkeleton() {
    const blocks = Array.from({ length: 5 })
      .map(
        () => `
        <div class="skeleton-card">
          <div class="skeleton skeleton-line w-30"></div>
          <div class="skeleton skeleton-line w-90"></div>
          <div class="skeleton skeleton-line w-90"></div>
          <div class="skeleton skeleton-line w-60"></div>
          <div class="skeleton skeleton-block"></div>
        </div>`
      )
      .join("");
    els.feed.innerHTML = blocks;
  }

  function showSearchSkeleton(query) {
    const blocks = Array.from({ length: 4 })
      .map(
        () => `
        <div class="skeleton-card">
          <div class="skeleton skeleton-line w-30"></div>
          <div class="skeleton skeleton-line w-90"></div>
          <div class="skeleton skeleton-line w-60"></div>
        </div>`
      )
      .join("");
    els.feed.innerHTML = `
      <div class="search-header">
        <div class="search-header__title">正在搜索「${escapeHtml(query)}」…</div>
        <button class="search-header__back" data-search-back>← 返回推荐</button>
      </div>
      ${blocks}
    `;
    bindSearchBack();
  }

  function renderSearchResults(payload) {
    const items = payload.items || [];
    const q = payload.query || state.searchQuery || "";
    const sourceLabel =
      payload.source === "zhihu_open_api"
        ? "来自知乎开放平台"
        : payload.source === "mock_fallback"
        ? "演示数据（远端不可用）"
        : "演示数据";

    const headerHtml = `
      <div class="search-header">
        <div>
          <div class="search-header__title">「${escapeHtml(q)}」共 ${items.length} 条结果</div>
          <div class="search-header__source">${escapeHtml(sourceLabel)}</div>
        </div>
        <button class="search-header__back" data-search-back>← 返回推荐</button>
      </div>
    `;

    if (!items.length) {
      const reason = payload.empty_reason || payload.remote_empty_reason || "暂时没有相关结果，换个关键词试试";
      els.feed.innerHTML = `
        ${headerHtml}
        <div class="feed-empty">${escapeHtml(reason)}</div>
      `;
      bindSearchBack();
      return;
    }

    els.feed.innerHTML =
      headerHtml +
      items.map((item, idx) => renderSearchCard(item, idx)).join("");

    els.feed.querySelectorAll("[data-search-url]").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("button") || e.target.closest("a")) return;
        const url = card.dataset.searchUrl;
        if (url) {
          window.open(url, "_blank", "noopener");
        }
      });
    });
    bindSearchBack();
  }

  function renderSearchCard(item, idx) {
    const typeLabel =
      item.content_type === "Article"
        ? "文章"
        : item.content_type === "Answer"
        ? "回答"
        : item.content_type || "内容";

    const authorAvatar = item.author_avatar
      ? `<img class="search-card__avatar-img" src="${escapeHtml(item.author_avatar)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
      : `<span class="search-card__avatar" aria-hidden="true">${escapeHtml((item.author_name || "Z").slice(0, 1))}</span>`;

    const badgeHtml = item.author_badge_text
      ? `<span class="search-card__badge">${escapeHtml(item.author_badge_text)}</span>`
      : "";

    const titleHtml = item.url
      ? `<a class="search-card__title" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>`
      : `<span class="search-card__title">${escapeHtml(item.title)}</span>`;

    const commentsPreview = (item.comments || [])
      .slice(0, 2)
      .map(
        (c) => `<div class="search-card__comment">💬 ${escapeHtml(c.content)}</div>`
      )
      .join("");

    const editTime = item.edit_time
      ? `<span class="search-card__meta-item">🕘 ${escapeHtml(formatRelativeTime(item.edit_time))}</span>`
      : "";

    return `
      <article class="feed-card search-card fade-in-up"
               data-search-url="${escapeHtml(item.url || "")}"
               style="animation-delay:${Math.min(idx * 30, 360)}ms">
        <div class="search-card__topbar">
          ${authorAvatar}
          <span class="search-card__author">${escapeHtml(item.author_name || "知乎用户")}</span>
          ${badgeHtml}
          <span class="search-card__type">${escapeHtml(typeLabel)}</span>
        </div>
        <h3 class="search-card__title-wrap">${titleHtml}</h3>
        <p class="search-card__excerpt">${escapeHtml(item.excerpt || "")}</p>
        ${commentsPreview ? `<div class="search-card__comments">${commentsPreview}</div>` : ""}
        <div class="search-card__meta">
          <span class="search-card__meta-item is-vote">▲ ${item.vote_count || 0} 赞同</span>
          <span class="search-card__meta-item">💬 ${item.comment_count || 0} 评论</span>
          ${editTime}
          ${item.url ? `<a class="search-card__link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" style="margin-left:auto">在知乎打开 ↗</a>` : ""}
        </div>
      </article>
    `;
  }

  function renderHot(payload) {
    const items = payload.items || [];
    const sourceLabel = payload.source && payload.source !== "mock" ? "来自知乎开放平台" : "演示数据";
    const totalPages = Math.max(1, payload.total_pages || state.hotTotalPages || 1);
    const currentPage = (typeof payload.page === "number" ? payload.page : state.hotPage) + 1;
    const pageLabel = totalPages > 1 ? `第 ${currentPage} / ${totalPages} 页` : "";

    els.right.innerHTML = `
      <div class="card hot-card">
        <div class="hot-card__head">
          <span class="badge">HOT</span>
          <h3>知乎热榜</h3>
          <button class="refresh" data-hot-refresh title="点击查看下一页">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M17.65 6.35A7.95 7.95 0 0012 4a8 8 0 108 8h-2a6 6 0 11-1.76-4.24L13 11h7V4l-2.35 2.35z"/></svg>
            换一批
          </button>
        </div>
        <ol class="hot-list">
          ${items
            .map(
              (item) => `
              <li class="hot-list__item ${item.is_hot ? "is-hot" : ""}" data-url="${escapeHtml(item.url || "")}" data-title="${escapeHtml(item.title)}">
                <span class="hot-list__rank">${item.rank}</span>
                <div style="flex:1; min-width:0;">
                  <div class="hot-list__title">${escapeHtml(item.title)}</div>
                  <div class="hot-list__metric">${escapeHtml(item.metric || "")}</div>
                </div>
                ${item.is_hot ? '<span class="hot-list__icon">🔥</span>' : ""}
              </li>`
            )
            .join("")}
        </ol>
        <div style="padding: 8px 18px 14px; font-size: 12px; color: var(--color-text-3); display:flex; justify-content:space-between; gap:8px;">
          <span>${escapeHtml(sourceLabel)}</span>
          <span>${escapeHtml(pageLabel)}</span>
        </div>
      </div>
      <div class="card promo-card">
        <span class="tag">Zmate Tip</span>
        <h4>试试 Zmate 助手</h4>
        <p>右下角的 ✦ 按钮可以让 Zmate 给你今日热点速览，或者陪你聊任意话题。</p>
      </div>
    `;
    els.right.querySelectorAll(".hot-list__item").forEach((node) => {
      node.addEventListener("click", () => {
        const url = node.dataset.url;
        if (url) {
          window.open(url, "_blank", "noopener");
        } else {
          showToast("已复制：" + node.dataset.title);
        }
      });
    });
    els.right.querySelector("[data-hot-refresh]").addEventListener("click", nextHotPage);
  }
})();
