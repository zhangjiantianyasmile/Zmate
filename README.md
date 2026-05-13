# Zmate
知乎 mate，best mate！

## 项目说明
**<font color=red>项目开始run之前，需要</color>**
* rename config/API_KEY_demo.json to config/API_KEY.json
* 在config/API_KEY.json中配置相关数据


## 本地启动

```bash
bash webapp/run.sh
# 默认 http://0.0.0.0:5050
# 自定义：HOST=127.0.0.1 PORT=8080 bash webapp/run.sh
```

可选：把 DeepSeek key 填到 `config/API_KEY.json` 的 `deepseek_api_key` 字段，否则 Zmate 聊天会走 mock 数据。

---

## 三种公网访问方式

| 方案 | 地址形式 | HTTPS | 是否固定 | 适用场景 |
|---|---|---|---|---|
| A. cloudflared 临时隧道 | `https://*.trycloudflare.com` | ✅ | ❌ 每次重启变 | 快速演示一次性链接 |
| B. bore.pub 固定隧道 | `http://bore.pub:<port>` | ❌ | ✅ 端口固定 | 7 天内稳定演示，可接受 HTTP |
| C. GitHub Pages + 隧道 | `https://<user>.github.io/Zmate/` | ✅ | ✅ 永久固定 | 想要长期固定地址的最佳方案 |

### A. cloudflared 临时演示

```bash
bash webapp/run.sh        # 终端 1：本地后端
bash webapp/share.sh      # 终端 2：拿临时 HTTPS 地址
```

### B. bore.pub 固定地址

```bash
bash webapp/run.sh                            # 终端 1
bash webapp/share-fixed.sh                    # 终端 2，默认 http://bore.pub:17050
REMOTE_PORT=23456 bash webapp/share-fixed.sh  # 自定义端口
```

### C. GitHub Pages 永久固定地址（推荐）

**架构**：前端静态资源放 GitHub Pages（永久免费 HTTPS），后端 API 通过 cloudflared HTTPS 隧道指回你的本机。

#### 一次性配置（在 GitHub 仓库设置里）

1. 打开 `https://github.com/<user>/Zmate/settings/pages`
2. **Build and deployment → Source** 选择 **GitHub Actions**
3. 提交一次代码触发 `.github/workflows/pages.yml`，等绿勾出现
4. 拿到永久地址：`https://<user>.github.io/Zmate/`

#### 每次开演示前

```bash
# 终端 1：起本地后端
bash webapp/run.sh

# 终端 2：起 cloudflared 拿一个 HTTPS 地址，并自动写入 config.js
AUTO_WRITE=1 bash webapp/share.sh

# 终端 3：把更新过的 config.js 提交（GitHub Actions 会自动重新部署）
git add webapp/static/config.js
git commit -m "chore: update Zmate apiBase"
git push
```

> **为什么必须 cloudflared 而不是 bore.pub？**
> GitHub Pages 强制 HTTPS，浏览器的 Mixed Content 策略会拒绝 HTTPS 页面调 HTTP 接口，所以后端必须也是 HTTPS。bore.pub 只有 HTTP，会被浏览器拦截。

> **想要后端隧道地址也固定？**
> 升级到 cloudflared **named tunnel**（需要一个域名托管在 Cloudflare）。配好之后后端固定到 `https://<sub>.<your-domain>.com`，把 `webapp/static/config.js` 里的 `apiBase` 改成它，从此前后端都永久固定。

---

## 文件结构

```
config/                  # 后端配置
document/                # 接口文档
webapp/
  ├─ run.sh              # 启动后端
  ├─ share.sh            # cloudflared 临时 HTTPS 隧道
  ├─ share-fixed.sh      # bore.pub 固定 HTTP 隧道
  ├─ server.py           # Flask + SSE 后端入口
  ├─ services/           # 业务模块（DeepSeek、热榜抓取、配置加载）
  └─ static/             # 纯静态前端（可托管到 GitHub Pages）
      ├─ config.js       # 前端运行时配置（apiBase 指向后端）
      ├─ index.html
      ├─ detail.html
      ├─ css/  js/  img/
.github/workflows/
  └─ pages.yml           # 自动把 webapp/static 部署到 GitHub Pages
```
