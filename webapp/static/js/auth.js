/**
 * Zmate 知乎账号登录态管理
 * --------------------------------------------------
 * - 启动时拉 /api/auth/me 判断当前是否已登录；
 * - 未登录：把 header 右侧的「我」头像换成「知乎登录」按钮；
 * - 已登录：渲染头像（avatar_path 优先，否则用昵称首字）和悬浮菜单（含「退出登录」）；
 * - 后端没配置 OAuth 时，按钮变灰并提示。
 *
 * 注意：跨源（apiBase 指向另一域名）时需要 fetch 带 credentials，
 * 否则浏览器不会带 session cookie，登录态读不到。
 */
(function () {
  const apiBase = ((window.ZMATE_CONFIG || {}).apiBase || "").replace(/\/+$/, "");

  function abs(path) {
    return apiBase + path;
  }

  async function fetchMe() {
    try {
      const resp = await fetch(abs("/api/auth/me"), {
        credentials: "include",
        cache: "no-store",
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      return null;
    }
  }

  function buildLoginUrl(meta) {
    const next = location.pathname + location.search + location.hash;
    const url = abs(meta.login_url || "/auth/zhihu/login");
    return url + (url.includes("?") ? "&" : "?") + "next=" + encodeURIComponent(next);
  }

  function buildLogoutUrl(meta) {
    const url = abs(meta.logout_url || "/auth/zhihu/logout");
    return url + (url.includes("?") ? "&" : "?") + "next=" + encodeURIComponent("/");
  }

  function ensureMenuStyles() {
    if (document.getElementById("zmate-auth-style")) return;
    const style = document.createElement("style");
    style.id = "zmate-auth-style";
    style.textContent = `
      .zmate-auth-btn {
        height: 36px; padding: 0 16px; border-radius: 18px;
        background: #1772f6; color: #fff; border: none; cursor: pointer;
        font-size: 14px; line-height: 36px; text-decoration: none;
        display: inline-flex; align-items: center; gap: 6px;
      }
      .zmate-auth-btn[disabled] { background: #c7c9d1; cursor: not-allowed; }
      .zmate-auth-btn:hover:not([disabled]) { background: #135ed1; }
      .zmate-user { position: relative; display: inline-flex; align-items: center; }
      .zmate-user__avatar {
        width: 34px; height: 34px; border-radius: 50%;
        background: linear-gradient(135deg, #1772f6, #5b8def);
        color: #fff; display: inline-flex; align-items: center; justify-content: center;
        font-size: 14px; font-weight: 600; cursor: pointer; overflow: hidden;
        border: 2px solid transparent;
      }
      .zmate-user__avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .zmate-user__avatar:hover { border-color: rgba(23, 114, 246, 0.4); }
      .zmate-user__menu {
        position: absolute; right: 0; top: calc(100% + 8px);
        background: #fff; border-radius: 10px; min-width: 180px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12); padding: 8px 0;
        display: none; z-index: 200;
      }
      .zmate-user__menu.is-open { display: block; }
      .zmate-user__name { padding: 8px 14px; font-size: 13px; color: #444;
        border-bottom: 1px solid #eef0f2; margin-bottom: 4px; }
      .zmate-user__item { display: block; padding: 8px 14px; font-size: 13px;
        color: #333; text-decoration: none; cursor: pointer; }
      .zmate-user__item:hover { background: #f4f6fa; color: #1772f6; }
    `;
    document.head.appendChild(style);
  }

  function render(slot, meta) {
    ensureMenuStyles();
    slot.innerHTML = "";

    if (meta && meta.authenticated && meta.user) {
      const user = meta.user;
      const wrap = document.createElement("div");
      wrap.className = "zmate-user";

      const avatar = document.createElement("button");
      avatar.type = "button";
      avatar.className = "zmate-user__avatar";
      avatar.title = user.name || "知乎用户";
      if (user.avatar) {
        const img = document.createElement("img");
        img.src = user.avatar;
        img.alt = user.name || "";
        img.referrerPolicy = "no-referrer";
        img.onerror = () => {
          avatar.textContent = (user.name || "我").slice(0, 1);
        };
        avatar.appendChild(img);
      } else {
        avatar.textContent = (user.name || "我").slice(0, 1);
      }

      const menu = document.createElement("div");
      menu.className = "zmate-user__menu";
      menu.innerHTML = `
        <div class="zmate-user__name">${(user.name || "知乎用户").replace(/[<>&]/g, "")}</div>
        <a class="zmate-user__item" href="https://www.zhihu.com/people/${encodeURIComponent(user.uid || "")}" target="_blank" rel="noopener">查看知乎主页</a>
        <a class="zmate-user__item" href="${buildLogoutUrl(meta)}">退出登录</a>
      `;

      avatar.addEventListener("click", (ev) => {
        ev.stopPropagation();
        menu.classList.toggle("is-open");
      });
      document.addEventListener("click", (ev) => {
        if (!wrap.contains(ev.target)) menu.classList.remove("is-open");
      });

      wrap.appendChild(avatar);
      wrap.appendChild(menu);
      slot.appendChild(wrap);
      return;
    }

    const link = document.createElement("a");
    link.className = "zmate-auth-btn";
    if (meta && meta.oauth_ready) {
      link.href = buildLoginUrl(meta);
      link.textContent = "知乎账号登录";
    } else {
      link.href = "javascript:void(0)";
      link.setAttribute("disabled", "disabled");
      link.textContent = "登录未启用";
      link.title = "后端尚未配置 zhihu_oauth_app_id / app_key / redirect_uri";
    }
    slot.appendChild(link);
  }

  function findSlot() {
    const right = document.querySelector(".app-header__right");
    if (!right) return null;
    let slot = right.querySelector("[data-auth-slot]");
    if (slot) return slot;
    // 把原模板里的「提问」按钮和静态头像替换为我们托管的 slot；
    // 不破坏其它图标按钮（通知、消息、回到首页等）。
    const cta = right.querySelector(".app-cta:not(.app-cta--ghost)");
    const avatar = right.querySelector(".app-avatar");
    slot = document.createElement("div");
    slot.dataset.authSlot = "1";
    slot.style.display = "inline-flex";
    slot.style.alignItems = "center";
    if (avatar) {
      avatar.replaceWith(slot);
    } else if (cta) {
      cta.insertAdjacentElement("afterend", slot);
    } else {
      right.appendChild(slot);
    }
    if (cta && !cta.dataset.keepCta) {
      // 「提问」是占位演示，登录前先隐藏，避免和登录按钮抢视觉焦点。
      cta.style.display = "none";
    }
    return slot;
  }

  async function init() {
    const slot = findSlot();
    if (!slot) return;
    render(slot, { authenticated: false, oauth_ready: true, login_url: "/auth/zhihu/login" });
    const meta = await fetchMe();
    render(slot, meta || { authenticated: false, oauth_ready: false });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
