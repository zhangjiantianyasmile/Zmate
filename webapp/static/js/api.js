(function (global) {
  function getApiBase() {
    const cfg = global.ZMATE_CONFIG || {};
    const base = (cfg.apiBase || "").trim();
    return base.replace(/\/+$/, "");
  }

  async function getJSON(url, opts) {
    const resp = await fetch(getApiBase() + url, opts);
    if (!resp.ok) {
      throw new Error("请求失败 " + resp.status);
    }
    return resp.json();
  }

  function escapeHtml(value) {
    if (value == null) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatRelativeTime(unix) {
    if (!unix) return "";
    const now = Math.floor(Date.now() / 1000);
    const diff = Math.max(1, now - unix);
    if (diff < 60) return diff + " 秒前";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
    const date = new Date(unix * 1000);
    return (
      date.getFullYear() +
      "-" +
      String(date.getMonth() + 1).padStart(2, "0") +
      "-" +
      String(date.getDate()).padStart(2, "0")
    );
  }

  function showToast(message, duration) {
    const container = document.querySelector(".toast") || (function () {
      const div = document.createElement("div");
      div.className = "toast";
      document.body.appendChild(div);
      return div;
    })();
    container.textContent = message;
    container.classList.add("is-visible");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => container.classList.remove("is-visible"), duration || 1800);
  }

  /**
   * Connect to a server-sent event endpoint via fetch streaming.
   * onDelta: (text) => void
   * onDone: () => void
   */
  async function streamPost(url, body, { onDelta, onDone, onError, signal }) {
    try {
      const resp = await fetch(getApiBase() + url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal,
      });
      if (!resp.ok || !resp.body) {
        throw new Error("Zmate 服务异常 " + resp.status);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const event = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const lines = event.split("\n");
          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;
            try {
              const parsed = JSON.parse(payload);
              if (parsed.delta) onDelta && onDelta(parsed.delta);
              if (parsed.done) {
                onDone && onDone();
                return;
              }
            } catch (e) {
              // ignore malformed chunk
            }
          }
        }
      }
      onDone && onDone();
    } catch (err) {
      if (err.name === "AbortError") return;
      onError && onError(err);
    }
  }

  global.ZmateAPI = {
    getJSON,
    streamPost,
    escapeHtml,
    formatRelativeTime,
    showToast,
  };
})(window);
