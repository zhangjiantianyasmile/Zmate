#!/usr/bin/env bash
# 一键把本地 Zmate webapp 暴露到公网（用于临时演示，无需服务器/域名/账号）
# 实现方式：cloudflared 的 quick tunnel 模式（trycloudflare.com）
#
# 用法：
#   bash share.sh                  # 默认隧道转发到 http://127.0.0.1:5050
#   PORT=8080 bash share.sh        # 自定义端口
#   AUTO_WRITE=1 bash share.sh     # 拿到 URL 后自动写入 webapp/static/config.js
#                                  # 适用于 GitHub Pages 部署：把后端 HTTPS 地址写好提交即可
#
# 注意：trycloudflare 的链接是临时的，进程退出后失效；适合演示，不要做正式部署。

set -e
cd "$(dirname "$0")"

PORT="${PORT:-5050}"
AUTO_WRITE="${AUTO_WRITE:-0}"
CONFIG_FILE="static/config.js"
LOG_FILE="${TMPDIR:-/tmp}/zmate-share.log"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[share] 未检测到 cloudflared，正在尝试通过 Homebrew 安装 ..."
  if ! command -v brew >/dev/null 2>&1; then
    echo "[share] 没有 Homebrew，请手动安装 cloudflared:"
    echo "        https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
  HOMEBREW_NO_AUTO_UPDATE=1 brew install cloudflared
fi

if ! lsof -iTCP:"${PORT}" -sTCP:LISTEN -nP >/dev/null 2>&1; then
  echo "[share] 警告：端口 ${PORT} 当前未监听，请先在另一个终端执行 bash run.sh 启动服务。"
  echo "[share] 仍将继续创建隧道（隧道会持续等待本地服务上线）..."
fi

echo "[share] 正在创建临时公网隧道 -> http://127.0.0.1:${PORT}"
echo "[share] 等待几秒后，控制台会打印类似 https://xxxxx.trycloudflare.com 的地址。"
echo "[share] Ctrl+C 退出隧道，链接立即失效。"
echo

cleanup() {
  if [ -n "${CF_PID:-}" ]; then
    kill "$CF_PID" 2>/dev/null || true
  fi
  exit 0
}
trap cleanup INT TERM

: > "$LOG_FILE"
cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate > "$LOG_FILE" 2>&1 &
CF_PID=$!

URL=""
for _ in $(seq 1 30); do
  sleep 1
  URL=$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | head -n1 || true)
  [ -n "${URL:-}" ] && break
done

if [ -z "${URL:-}" ]; then
  echo "[share] 隧道未能在 30 秒内分配地址，输出日志："
  cat "$LOG_FILE"
  wait "$CF_PID"
  exit 1
fi

echo "============================================================"
echo "  公网 HTTPS 地址：${URL}"
echo "============================================================"
echo
echo "想配合 GitHub Pages 静态前端使用？把 webapp/static/config.js 改成："
echo
echo "  window.ZMATE_CONFIG = { apiBase: \"${URL}\" };"
echo

if [ "${AUTO_WRITE}" = "1" ]; then
  cat > "${CONFIG_FILE}" <<EOF
/**
 * Zmate 前端运行时配置（由 share.sh 自动生成）
 * 把 apiBase 指向当前 cloudflared 隧道地址，便于 GitHub Pages 静态前端调用本地后端。
 * 本地直跑后端时可恢复为空字符串。
 */
window.ZMATE_CONFIG = {
  apiBase: "${URL}"
};
EOF
  echo "[share] 已自动写入 ${CONFIG_FILE}，提交并 push 到 GitHub 即可让 Pages 前端连上。"
  echo "        提交命令示例："
  echo "          git add ${CONFIG_FILE} && git commit -m 'chore: update Zmate apiBase' && git push"
fi

wait "$CF_PID"
