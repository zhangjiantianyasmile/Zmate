# Zmate Webapp

这是 [Zmate](../README.md) 项目的实际实现：一个知乎首页的 Web 应用，并集成了名为 **Zmate** 的 AI 知识助手。

## 功能概览

- **首页（`/`）**：知乎风格的三栏布局
  - 顶部：Logo / 主导航 / 搜索 / 用户区
  - 左栏：内容分类筛选 + 推广位
  - 中栏：推荐 feed（多种卡片：回答 / 文章 / 视频 / 想法），支持「换一批」刷新动画与骨架屏
  - 右栏：知乎热榜（优先抓取公开热榜接口，失败时回退到本地数据）
- **文档详情页（`/detail.html?id=...`）**
  - 问答详情页样式（问题 + 作者 + 内容 + 标签 + 操作 + 评论区 + 相关推荐）
  - 自动把当前文档信息注入到 Zmate 上下文
- **Zmate 助手（悬浮按钮 + 弹出聊天面板）**
  - 在首页：点击「✦ 今日热点」自动拉取热榜并由 Zmate 给出今日观察
  - 在详情页：自动携带文章标题/作者/摘要，可基于这篇内容做提炼、辩论、追问
  - 支持流式（SSE）回复
  - 配置 DeepSeek API Key 后可获得真实大模型回答；未配置时使用本地规则化 mock 流式回复

## 目录结构

```
webapp/
├── README.md                此文档
├── requirements.txt         Python 依赖
├── config.example.json      配置示例（复制为 config.json 后填入密钥）
├── server.py                Flask 入口
├── services/
│   ├── config_loader.py     合并 webapp/config.json 与 ../config/API_KEY.json
│   ├── deepseek_client.py   DeepSeek Chat 客户端 + 本地 mock 回复
│   ├── hot_list.py          公开热榜抓取 + 缓存 + 回退
│   └── mock_data.py         feed / 详情 / 评论的 mock 数据
└── static/
    ├── index.html           首页
    ├── detail.html          文档详情页
    ├── css/                 通用 / 首页 / 详情 / Zmate 样式
    ├── js/                  通用 / Zmate / 首页 / 详情逻辑
    └── img/                 站点图标等静态资源
```

## 快速开始

> 需要 Python 3.9+

```bash
cd webapp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

启动后默认监听 `http://127.0.0.1:5050/`。

## 配置 API Key（可选，但推荐）

仓库根目录已有 `config/API_KEY.json`，存放着 Zhihu 开放平台的 `app_secret`。本项目额外读取 `webapp/config.json`，用于补充 DeepSeek 等密钥：

```bash
cp config.example.json config.json
```

然后编辑 `webapp/config.json`：

```json
{
  "deepseek_api_key": "你的 DeepSeek API Key",
  "deepseek_base_url": "https://api.deepseek.com/v1",
  "deepseek_model": "deepseek-chat",
  "zhihu_app_key": "可选，用户 token，用于调用知乎社区 API"
}
```

也可以直接通过环境变量覆盖：

```bash
export DEEPSEEK_API_KEY=sk-xxx
export DEEPSEEK_MODEL=deepseek-chat
python server.py
```

> 不填 `deepseek_api_key` 也能运行，Zmate 将用本地规则化的流式回复模拟体验。

## 路由说明

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `/` | GET | 首页 |
| `/detail.html?id=<id>` | GET | 文档详情页（id 可任意，会被稳定 hash 成相同内容） |
| `/health` | GET | 检查 DeepSeek/知乎密钥加载情况 |
| `/api/feed` | GET | 推荐 feed（每次随机生成） |
| `/api/hot` | GET | 热榜（缓存 5 分钟，可加 `?refresh=1` 强制刷新） |
| `/api/document/<id>` | GET | 文档详情 |
| `/api/zmate/news` | GET | Zmate 抓取并选出今日热点 TOP3 |
| `/api/zmate/chat` | POST | Zmate 对话（SSE 流） |

## 设计取舍

- **热榜数据**：调用了开源聚合接口（`api-hot.efefee.cn`、`api.vvhan.com`）拿真实知乎热榜，失败时回退到本地 mock，保证离线可用。
- **首页内容**：使用本地随机生成的中文 mock 文案，每次请求 seed 不同，刷新时具备视觉变化与动画。
- **Zmate 流式输出**：通过 `fetch + ReadableStream` 解析 SSE，避免引入额外依赖。

