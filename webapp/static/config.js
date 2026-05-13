/**
 * Zmate 前端运行时配置
 * --------------------------------------------------
 * apiBase 用来告诉前端 /api/* 该往哪发：
 *   - 留空 ""        ：和当前页面同源（本地直接 python server.py 时用这个）
 *   - "https://xxxx" ：发到指定地址（GitHub Pages 等纯静态部署时填后端隧道地址）
 *
 * 部署到 GitHub Pages 时，需要把 apiBase 改成你的后端 HTTPS 地址，例如：
 *   apiBase: "https://apparel-supervisor-neighbors-combining.trycloudflare.com"
 *
 * 必须是 HTTPS！HTTP 会被浏览器以 Mixed Content 拒绝。
 */
window.ZMATE_CONFIG = {
  apiBase: ""
};
