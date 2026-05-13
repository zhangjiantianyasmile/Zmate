#!/usr/bin/env bash
# 把本地 Zmate webapp 暴露到一个 7 天内稳定的固定公网地址
#
# 使用 bore.pub 作为反向代理（开源 + 公益服务器 + 完全免费 + 无警告页）：
#   - 客户端从 Homebrew 安装：brew install bore-cli
#   - 给定的 REMOTE_PORT 只要没被别人抢占，地址就一直是 http://bore.pub:<REMOTE_PORT>
#   - 进程不退，地址不变；脚本带断线自动重连
#
# 用法：
#   bash share-fixed.sh                                  # 默认 PORT=5050, REMOTE_PORT=17050
#   REMOTE_PORT=23456 bash share-fixed.sh                # 自定义远端端口（建议 1024-65535）
#   PORT=8080 REMOTE_PORT=18080 bash share-fixed.sh      # 自定义本地端口
#
# 退出：Ctrl+C
#
# 备选方案（如 bore.pub 抽风）：
#   bash share.sh   # 走 cloudflared quick tunnel，地址每次变但稳定性好
#
# 注意：
#   - 通过 bore.pub 的流量是 HTTP（非 HTTPS）。仅用于演示，不要传敏感数据。
#   - 远端端口是"先到先得"，建议挑一个不太常见的数字降低被占概率。

set -u
cd "$(dirname "$0")"

PORT="${PORT:-5050}"
REMOTE_PORT="${REMOTE_PORT:-17050}"
BORE_SERVER="${BORE_SERVER:-bore.pub}"

if ! command -v bore >/dev/null 2>&1; then
  echo "[fixed] 未检测到 bore，正在通过 Homebrew 安装 ..."
  if ! command -v brew >/dev/null 2>&1; then
    echo "[fixed] 没有 Homebrew，请手动安装："
    echo "        cargo install bore-cli   或者参考 https://github.com/ekzhang/bore"
    exit 1
  fi
  HOMEBREW_NO_AUTO_UPDATE=1 brew install bore-cli
fi

print_header() {
  echo "============================================================"
  echo "  Zmate 固定公网链接 (bore.pub)"
  echo "  本地端口      : ${PORT}"
  echo "  公网地址      : http://${BORE_SERVER}:${REMOTE_PORT}"
  echo "  健康检查      : http://${BORE_SERVER}:${REMOTE_PORT}/health"
  echo
  echo "  【知乎账号登录】"
  echo "  入口          : http://${BORE_SERVER}:${REMOTE_PORT}/auth/zhihu/login"
  echo "  请把下面这个『知乎登录回调地址』填到 https://www.zhihu.com/ring/moltbook 对应的 OAuth 应用里："
  echo "      http://${BORE_SERVER}:${REMOTE_PORT}/auth/zhihu/callback"
  echo "  本地直连时同样能用："
  echo "      http://127.0.0.1:${PORT}/auth/zhihu/callback"
  echo "  （后端会按访问入口自动选择，两个都注册到知乎后台即可同时生效。）"
  echo
  echo "  Ctrl+C 退出脚本"
  echo "============================================================"
}

cleanup() {
  echo
  echo "[fixed] 收到退出信号，关闭隧道。"
  if [ -n "${BORE_PID:-}" ]; then
    kill "$BORE_PID" 2>/dev/null || true
  fi
  exit 0
}
trap cleanup INT TERM

print_header

if ! lsof -iTCP:"${PORT}" -sTCP:LISTEN -nP >/dev/null 2>&1; then
  echo "[fixed] 警告：本地端口 ${PORT} 当前未监听。请在另一个终端先执行 bash run.sh 启动服务。"
  echo "[fixed] 仍将创建隧道，bore 会持续等待本地服务上线。"
fi

FAIL_COUNT=0
while true; do
  echo "[fixed] 启动 bore 隧道 ..."
  bore local "${PORT}" --to "${BORE_SERVER}" --port "${REMOTE_PORT}" &
  BORE_PID=$!

  sleep 4
  HTTP_CODE=$(curl -sS -o /dev/null --max-time 8 -w "%{http_code}" "http://${BORE_SERVER}:${REMOTE_PORT}/health" || echo "000")
  if [ "${HTTP_CODE}" = "200" ]; then
    echo "[fixed] OK：公网地址已可访问，HTTP ${HTTP_CODE}"
    FAIL_COUNT=0
  else
    echo "[fixed] 警告：公网地址当前返回 HTTP ${HTTP_CODE}（可能 bore.pub 端口被占或本地服务未就绪）"
  fi

  wait "$BORE_PID" 2>/dev/null
  EXIT_CODE=$?

  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "[fixed] 隧道断开（exit=${EXIT_CODE}），第 ${FAIL_COUNT} 次重试，5 秒后重连 ..."
  sleep 5
done
