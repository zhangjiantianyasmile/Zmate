#!/usr/bin/env bash
# 启动 Zmate webapp 本地服务
# 默认监听 0.0.0.0:5050（公网/局域网均可访问），可通过环境变量 HOST / PORT 覆盖
# 使用：bash run.sh

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[run] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "[run] 安装依赖 ..."
pip install -q -r requirements.txt

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5050}"

echo "[run] 启动服务：http://${HOST}:${PORT}/"
echo "[run] 局域网内其他设备可访问：http://$(ipconfig getifaddr en0 2>/dev/null || hostname):${PORT}/"
python server.py
