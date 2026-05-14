(function () {
  const { getJSON, escapeHtml, formatRelativeTime, showToast } = window.ZmateAPI;

  document.addEventListener("DOMContentLoaded", async () => {
    const params = new URLSearchParams(location.search);
    const id = params.get("id") || "demo-default";
    const main = document.querySelector("[data-detail-main]");
    const side = document.querySelector("[data-detail-side]");
    const commentBox = document.querySelector("[data-comments]");

    main.innerHTML = `<div class="detail-loading">正在为你加载这篇内容…</div>`;

    bindSearchForm();

    let zmate = new window.Zmate({});

    try {
      const doc = await getJSON(`/api/document/${encodeURIComponent(id)}`);
      renderDocument(main, doc);
      renderSide(side, doc);
      renderComments(commentBox, doc);

      zmate.setDocument({
        id: doc.id,
        title: doc.title,
        author: doc.author,
        excerpt: (doc.paragraphs || [])[0] || "",
        // 完整正文随 setDocument 一起带进来；后端在挑到直答 / moonshot-v1-8k
        // 时会用整段正文替换 excerpt，其它模型仍按摘要拼 system prompt。
        paragraphs: doc.paragraphs || [],
      });

      const bubble = document.querySelector(".zmate-launcher__bubble");
      if (bubble) {
        bubble.textContent = "需要我帮你提炼这篇内容吗？";
        bubble.classList.remove("hidden");
      }
    } catch (err) {
      main.innerHTML = `<div class="detail-loading">加载失败：${escapeHtml(err.message)}</div>`;
    }
  });

  function bindSearchForm() {
    const form = document.querySelector("[data-search-form]");
    if (!form) return;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = form.querySelector("[data-search-input]");
      const q = (input ? input.value : "").trim();
      if (!q) {
        showToast("请输入搜索关键词");
        return;
      }
      location.href = `./?q=${encodeURIComponent(q)}`;
    });
  }

  function renderDocument(main, doc) {
    const author = doc.author || {};
    const tags = (doc.tags || [])
      .map((t) => `<span>#${escapeHtml(t)}</span>`)
      .join("");

    const paragraphs = (doc.paragraphs || [])
      .map((p) => `<p>${escapeHtml(p)}</p>`)
      .join("");

    const images = (doc.images || [])
      .map((src) => `<img src="${escapeHtml(src)}" alt="" loading="lazy" />`)
      .join("");

    main.innerHTML = `
      <article class="detail-card fade-in-up">
        <div class="detail-question">
          <h1>${escapeHtml(doc.question || doc.title)}</h1>
          <div class="detail-question__meta">
            <span>👀 ${doc.view_count.toLocaleString()} 次浏览</span>
            <span>👥 ${doc.follower_count.toLocaleString()} 关注者</span>
            <span>🕘 ${escapeHtml(formatRelativeTime(doc.publish_time))} 发布</span>
          </div>
        </div>

        <div class="detail-author">
          <span class="detail-author__avatar" style="background:${author.avatar_color || "#5b8def"}">${escapeHtml(author.initial || "Z")}</span>
          <div>
            <div class="detail-author__name">${escapeHtml(author.name || "知乎用户")}</div>
            <div class="detail-author__headline">${escapeHtml(author.headline || "")}</div>
          </div>
          <button class="detail-author__follow">+ 关注作者</button>
        </div>

        <div class="detail-content">
          ${paragraphs}
          ${images}
        </div>

        <div class="detail-tags">${tags}</div>

        <div class="detail-actions">
          <button class="detail-actions__btn is-primary" data-action="upvote">
            ▲ <span data-vote-count>${escapeHtml(doc.vote_count_text)}</span> 赞同
          </button>
          <button class="detail-actions__btn" data-action="downvote">▼ 反对</button>
          <button class="detail-actions__btn" data-action="favorite">★ 收藏 ${escapeHtml(doc.favorite_count_text)}</button>
          <button class="detail-actions__btn" data-action="comment">💬 ${escapeHtml(doc.comment_count_text)} 评论</button>
          <button class="detail-actions__btn" data-action="share" style="margin-left:auto">分享</button>
        </div>
      </article>
    `;

    main.querySelectorAll(".detail-actions__btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const act = btn.dataset.action;
        if (act === "upvote") {
          if (btn.classList.toggle("is-active")) {
            const span = btn.querySelector("[data-vote-count]");
            const text = span.textContent;
            const num = parseFloat(text) || 0;
            const newVal = (num + 0.1).toFixed(1);
            span.textContent = newVal + (text.includes("万") ? " 万" : "");
            showToast("已点赞，谢谢支持");
          } else {
            showToast("已取消点赞");
          }
          return;
        }
        if (act === "favorite") {
          showToast("已加入收藏夹");
          return;
        }
        if (act === "share") {
          if (navigator.clipboard) {
            navigator.clipboard.writeText(location.href).then(() => showToast("链接已复制"));
          } else {
            showToast("链接复制需要 https 环境");
          }
          return;
        }
        if (act === "downvote") {
          showToast("已反馈不感兴趣");
          return;
        }
        if (act === "comment") {
          document.querySelector("[data-comments]").scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
  }

  function renderSide(side, doc) {
    const related = (doc.related_questions || [])
      .map(
        (r) => `<li data-rel-id="${escapeHtml(r.id)}">
          <div>${escapeHtml(r.title)}</div>
          <small>${escapeHtml(r.excerpt)}</small>
        </li>`
      )
      .join("");

    side.innerHTML = `
      <div class="related-card">
        <h3>相关问题</h3>
        <ul>${related}</ul>
      </div>
      <div class="related-card">
        <h3>试试 Zmate</h3>
        <p style="margin:0; font-size:13px; color: var(--color-text-3); line-height:1.6;">
          点击右下角的 ✦ 按钮，把疑问、感受、需要解读的段落都丢给 Zmate，他会基于这篇内容陪你聊。
        </p>
      </div>
    `;

    side.querySelectorAll("[data-rel-id]").forEach((node) => {
      node.addEventListener("click", () => {
        location.href = `./detail.html?id=${encodeURIComponent(node.dataset.relId)}`;
      });
    });
  }

  function renderComments(box, doc) {
    if (!box) return;
    const list = (doc.comments || [])
      .map(
        (c) => {
          const author = c.author || {};
          return `
            <div class="comment-item">
              <span class="comment-item__avatar" style="background:${author.avatar_color || "#5b8def"}">${escapeHtml(author.initial || "Z")}</span>
              <div class="comment-item__body">
                <div class="comment-item__name">${escapeHtml(author.name || "用户")}</div>
                <div class="comment-item__content">${escapeHtml(c.content)}</div>
                <div class="comment-item__meta">
                  <span>👍 ${c.like_count}</span>
                  <span>💬 ${c.replies}</span>
                  <span>${escapeHtml(formatRelativeTime(c.publish_time))}</span>
                </div>
              </div>
            </div>
          `;
        }
      )
      .join("");

    box.innerHTML = `
      <h2>${escapeHtml(doc.comment_count_text)} 条评论</h2>
      <div class="comment-input">
        <textarea placeholder="说点什么…（演示模式，不会真正发送）" data-comment-input></textarea>
        <button data-comment-submit>评论</button>
      </div>
      <div class="comment-list">${list}</div>
    `;

    box.querySelector("[data-comment-submit]").addEventListener("click", () => {
      const text = box.querySelector("[data-comment-input]").value.trim();
      if (!text) {
        showToast("评论不能为空");
        return;
      }
      const list = box.querySelector(".comment-list");
      const node = document.createElement("div");
      node.className = "comment-item fade-in-up";
      node.innerHTML = `
        <span class="comment-item__avatar" style="background:#1772f6">我</span>
        <div class="comment-item__body">
          <div class="comment-item__name">本地访客</div>
          <div class="comment-item__content">${escapeHtml(text)}</div>
          <div class="comment-item__meta">
            <span>👍 0</span>
            <span>💬 0</span>
            <span>刚刚</span>
          </div>
        </div>
      `;
      list.prepend(node);
      box.querySelector("[data-comment-input]").value = "";
      showToast("评论已发布（演示）");
    });
  }
})();
