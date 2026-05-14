(function (global) {
  const { streamPost, getJSON, escapeHtml, showToast } = global.ZmateAPI;

  const SUGGESTIONS_DEFAULT = [
    "今天有什么值得关注的热点？",
    "帮我列出三个 AI 行业最近的关键变化",
    "我最近精力不足，怎么调整？",
    "推荐一篇关于职业规划的好答案",
  ];

  const SUGGESTIONS_DOC = [
    "帮我提炼摘要",
    "这篇里最有争议的观点是什么？",
    "里面有哪些可以验证的事实和数据？",
    "对普通从业者来说有哪些可执行建议？",
  ];

  function buildPanel() {
    const panel = document.createElement("div");
    panel.className = "zmate-panel";
    panel.innerHTML = `
      <div class="zmate-panel__header">
        <div class="zmate-panel__avatar">Z</div>
        <div class="zmate-panel__title">
          <strong>Zmate</strong>
          <span data-zmate-status>知乎 Mate · best mate</span>
        </div>
        <button class="zmate-panel__action" data-zmate-news title="今日热点">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L3 14h7l-1 8 11-13h-8z"/></svg>
        </button>
        <button class="zmate-panel__action" data-zmate-clear title="清空">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M19 4h-3.5l-1-1h-5l-1 1H5v2h14V4zm-12 3v12c0 1.1.9 2 2 2h6c1.1 0 2-.9 2-2V7H7z"/></svg>
        </button>
        <button class="zmate-panel__action" data-zmate-close title="关闭">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M19 6.4L17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z"/></svg>
        </button>
      </div>
      <div class="zmate-context hidden" data-zmate-context>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg>
        <span class="zmate-context__title">正在围绕：<span data-zmate-context-title></span></span>
      </div>
      <div class="zmate-messages" data-zmate-messages></div>
      <div class="zmate-suggestions" data-zmate-suggestions></div>
      <div class="zmate-input">
        <div class="zmate-input__toolbar">
          <label class="zmate-model" title="选择对话使用的大模型">
            <span class="zmate-model__icon" aria-hidden="true">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a5 5 0 0 0-5 5v1H6a3 3 0 0 0-3 3v3a3 3 0 0 0 3 3h1v1a5 5 0 0 0 10 0v-1h1a3 3 0 0 0 3-3v-3a3 3 0 0 0-3-3h-1V7a5 5 0 0 0-5-5zm-3 5a3 3 0 1 1 6 0v10a3 3 0 1 1-6 0V7z"/></svg>
            </span>
            <select data-zmate-model></select>
          </label>
          <span class="zmate-model__hint" data-zmate-model-hint></span>
        </div>
        <div class="zmate-input__row">
          <textarea data-zmate-input rows="1" placeholder="问问 Zmate（Enter 发送 / Shift+Enter 换行）"></textarea>
          <button class="zmate-input__send" data-zmate-send disabled>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
            发送
          </button>
        </div>
      </div>
    `;
    return panel;
  }

  function buildLauncher() {
    const launcher = document.createElement("div");
    launcher.className = "zmate-launcher";
    launcher.innerHTML = `
      <div class="zmate-launcher__bubble" data-zmate-bubble>
        嗨，我是 Zmate，可以帮你看今天的热点。
      </div>
      <button class="zmate-launcher__btn" data-zmate-toggle aria-label="打开 Zmate">
        <span class="zmate-launcher__pulse" aria-hidden="true"></span>
      </button>
    `;
    return launcher;
  }

  const MODEL_STORAGE_KEY = "zmate.model.selection.v1";
  const FALLBACK_MODELS = [
    {
      provider: "zhida",
      model: "zhida-thinking-1p5",
      label: "知乎直答 · 深度思考",
      short: "直答·深度",
      ready: true,
      default: true,
    },
    {
      provider: "zhida",
      model: "zhida-fast-1p5",
      label: "知乎直答 · 快速回答",
      short: "直答·快速",
      ready: true,
      default: false,
    },
    {
      provider: "deepseek",
      model: "deepseek-chat",
      label: "DeepSeek",
      short: "DeepSeek",
      ready: false,
      default: false,
    },
  ];

  class ZmateAssistant {
    constructor(opts) {
      this.opts = opts || {};
      this.history = [];
      this.streaming = false;
      this.abortCtl = null;
      this.document = this.opts.document || null;
      // 详情页脚本可以在 new Zmate 时立刻传 inDocumentPage:true，避免文档异步
      // 加载期间「今日热点」按钮短暂闪一下。即便没有这个标记，setDocument
      // 触发的 applyDocumentContext() 也会兜底隐藏。
      this.inDocumentPage = !!this.opts.inDocumentPage;
      this.models = [];
      this.selectedModel = null;
      this.mount();
      this.loadModels();
      this.greet();
    }

    mount() {
      this.launcher = buildLauncher();
      this.panel = buildPanel();
      document.body.appendChild(this.launcher);
      document.body.appendChild(this.panel);

      this.$bubble = this.launcher.querySelector("[data-zmate-bubble]");
      this.$messages = this.panel.querySelector("[data-zmate-messages]");
      this.$status = this.panel.querySelector("[data-zmate-status]");
      this.$context = this.panel.querySelector("[data-zmate-context]");
      this.$contextTitle = this.panel.querySelector("[data-zmate-context-title]");
      this.$input = this.panel.querySelector("[data-zmate-input]");
      this.$send = this.panel.querySelector("[data-zmate-send]");
      this.$suggestions = this.panel.querySelector("[data-zmate-suggestions]");
      this.$modelSelect = this.panel.querySelector("[data-zmate-model]");
      this.$modelHint = this.panel.querySelector("[data-zmate-model-hint]");
      // 顶栏的「今日热点」入口按钮。文档详情页打开 Zmate 时会被 applyDocumentContext()
      // 隐藏——围绕单篇内容的对话场景里不再弹出与之无关的热点选拔。
      this.$newsBtn = this.panel.querySelector("[data-zmate-news]");

      this.launcher.querySelector("[data-zmate-toggle]").addEventListener("click", () => this.toggle());
      this.panel.querySelector("[data-zmate-close]").addEventListener("click", () => this.close());
      this.panel.querySelector("[data-zmate-clear]").addEventListener("click", () => this.clearChat());
      this.$newsBtn.addEventListener("click", () => this.askNews());

      this.$input.addEventListener("input", () => {
        this.$send.disabled = !this.$input.value.trim() || this.streaming;
        this.autosize();
      });
      this.$input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this.send();
        }
      });
      this.$send.addEventListener("click", () => this.send());

      this.$modelSelect.addEventListener("change", () => this.onModelChange());

      this.renderSuggestions();
      this.applyDocumentContext();
    }

    loadModels() {
      this.renderModels(FALLBACK_MODELS);
      getJSON("/api/zmate/models")
        .then((data) => {
          const models = (data && data.models) || [];
          if (!models.length) return;
          this.renderModels(models, data && data.default);
        })
        .catch(() => {
          // 静默：保留 FALLBACK_MODELS，避免阻塞用户聊天。
        });
    }

    renderModels(models, defaultSel) {
      this.models = models.slice();
      let savedKey = "";
      try {
        savedKey = localStorage.getItem(MODEL_STORAGE_KEY) || "";
      } catch (e) {
        savedKey = "";
      }

      const optionKey = (m) => `${m.provider}::${m.model}`;
      let chosen =
        this.models.find((m) => optionKey(m) === savedKey) ||
        this.models.find((m) => defaultSel && m.provider === defaultSel.provider && m.model === defaultSel.model) ||
        this.models.find((m) => m.default && m.ready) ||
        this.models.find((m) => m.ready) ||
        this.models[0];

      this.$modelSelect.innerHTML = this.models
        .map((m) => {
          const tag = m.ready ? "" : "（未接入·走兜底）";
          return `<option value="${escapeHtml(optionKey(m))}">${escapeHtml(m.label || m.short || m.model)}${tag}</option>`;
        })
        .join("");
      if (chosen) {
        this.$modelSelect.value = optionKey(chosen);
        this.selectedModel = chosen;
        this.updateModelHint(chosen);
      }
    }

    onModelChange() {
      const key = this.$modelSelect.value;
      const found = this.models.find((m) => `${m.provider}::${m.model}` === key);
      if (!found) return;
      this.selectedModel = found;
      this.updateModelHint(found);
      try {
        localStorage.setItem(MODEL_STORAGE_KEY, key);
      } catch (e) {
        // ignore
      }
    }

    updateModelHint(model) {
      if (!this.$modelHint) return;
      if (!model) {
        this.$modelHint.textContent = "";
        this.$modelHint.classList.remove("is-warn");
        return;
      }
      if (model.ready) {
        this.$modelHint.textContent = `当前：${model.short || model.label || model.model}`;
        this.$modelHint.classList.remove("is-warn");
      } else {
        this.$modelHint.textContent = `${model.short || model.label || model.model} 暂未配置 Key，将使用本地兜底回复`;
        this.$modelHint.classList.add("is-warn");
      }
    }

    autosize() {
      this.$input.style.height = "auto";
      this.$input.style.height = Math.min(this.$input.scrollHeight, 120) + "px";
    }

    renderSuggestions() {
      const list = this.document ? SUGGESTIONS_DOC : SUGGESTIONS_DEFAULT;
      this.$suggestions.innerHTML = list
        .map((s) => `<button class="zmate-suggestions__chip" type="button">${escapeHtml(s)}</button>`)
        .join("");
      this.$suggestions.querySelectorAll(".zmate-suggestions__chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          this.$input.value = chip.textContent;
          this.$input.focus();
          this.autosize();
          this.$send.disabled = false;
          this.send();
        });
      });
    }

    applyDocumentContext() {
      // 「在文档详情页打开 Zmate」满足任一即可：
      //   1) 调用方在 new 时显式声明 inDocumentPage（detail.js 在拿到文档前
      //      就能锁定，避免热点按钮短暂闪现）；
      //   2) 已经 setDocument 注入了具体文档。
      const inDocScene = this.inDocumentPage || !!this.document;
      if (this.$newsBtn) this.$newsBtn.hidden = inDocScene;

      if (this.document) {
        this.$context.classList.remove("hidden");
        this.$contextTitle.textContent = this.document.title || "当前文档";
        this.$status.textContent = "围绕当前阅读内容陪你聊";
      } else {
        this.$context.classList.add("hidden");
        this.$status.textContent = "知乎 Mate · best mate";
      }
    }

    setDocument(doc) {
      this.document = doc;
      this.history = [];
      this.applyDocumentContext();
      this.renderSuggestions();
      this.$messages.innerHTML = "";
      this.greet();
    }

    greet() {
      if (this.document) {
        this.appendAssistant(
          `已经看到你打开的《${this.document.title}》了～\n\n你可以让我：\n• 用 1 分钟口头摘要这篇内容\n• 找出可被验证的关键事实\n• 给出 3 点延伸阅读建议\n\n或者直接把你心里的疑问发给我。`
        );
      } else {
        this.appendAssistant(
          "嗨，我是 Zmate（知乎 Mate）👋\n\n点击右上角 ✦ 按钮可以让我推荐今天的热点；也可以直接把你想聊的话题发过来。"
        );
      }
    }

    open() {
      this.panel.classList.add("is-open");
      this.$bubble.classList.add("hidden");
      setTimeout(() => this.$input.focus(), 220);
    }

    close() {
      this.panel.classList.remove("is-open");
    }

    toggle() {
      if (this.panel.classList.contains("is-open")) this.close();
      else this.open();
    }

    clearChat() {
      this.history = [];
      this.$messages.innerHTML = "";
      this.greet();
    }

    appendUser(text) {
      const node = document.createElement("div");
      node.className = "zmate-msg is-user";
      node.innerHTML = `
        <div class="zmate-msg__avatar">我</div>
        <div class="zmate-msg__bubble">${escapeHtml(text)}</div>
      `;
      this.$messages.appendChild(node);
      this.$messages.scrollTop = this.$messages.scrollHeight;
    }

    appendAssistant(text) {
      const node = document.createElement("div");
      node.className = "zmate-msg is-zmate";
      node.innerHTML = `
        <div class="zmate-msg__avatar">Z</div>
        <div class="zmate-msg__bubble"></div>
      `;
      node.querySelector(".zmate-msg__bubble").textContent = text;
      this.$messages.appendChild(node);
      this.$messages.scrollTop = this.$messages.scrollHeight;
      return node;
    }

    appendNewsCard(picks, meta) {
      if (!picks || !picks.length) return;
      // 后端 hot_picks 返回的 model_used 可能是：moonshot-v1-8k / deepseek /
      // mock（无 key 或调用失败兜底）。这里按模型显示对应标签；mock 路径
      // 不渲染任何模型标签，避免给用户「演示数据」之类的误导——只有那条
      // 路径才真的不是大模型筛出来的。
      const modelUsed = (meta && meta.model_used) || "";
      let modelTag = "";
      if (modelUsed === "moonshot-v1-8k") {
        modelTag = '<span class="zmate-news__source" title="由 Moonshot v1 8k 提供计算">Zmate 精选</span>';
      } else if (modelUsed === "deepseek") {
        modelTag = '<span class="zmate-news__source" title="由 DeepSeek 提供计算">Zmate 精选</span>';
      } else if (modelUsed && modelUsed !== "mock") {
        modelTag = `<span class="zmate-news__source" title="由 ${escapeHtml(modelUsed)} 提供计算">Zmate 精选</span>`;
      }
      // const cacheTag =
      //   meta && meta.cache === "hit"
      //     ? '<span class="zmate-news__cache" title="命中本地缓存（15 分钟）">缓存</span>'
      //     : "";

      const wrap = document.createElement("div");
      wrap.className = "zmate-msg is-zmate";
      wrap.innerHTML = `
        <div class="zmate-msg__avatar">Z</div>
        <div class="zmate-news">
          <div class="zmate-news__title">
            <span>🔥 值得关注的热点 · TOP${picks.length}</span>
            <span class="zmate-news__meta">${modelTag}</span>
          </div>
          <div class="zmate-news__hint">来源：知乎热榜 Top 20 · 由 Zmate 帮你挑出最值得花时间的几条</div>
        </div>
      `;
      const news = wrap.querySelector(".zmate-news");
      picks.forEach((p, idx) => {
        const item = document.createElement("div");
        item.className = "zmate-news__pick";
        const reasonHtml = p.reason
          ? `<div class="zmate-news__reason">💡 ${escapeHtml(p.reason)}</div>`
          : "";
        item.innerHTML = `
          <span class="rank">${idx + 1}</span>
          <div class="zmate-news__pick-body">
            <div class="zmate-news__pick-title">${escapeHtml(p.title)}</div>
            <small class="zmate-news__pick-metric">${escapeHtml(p.metric || "")}</small>
            ${reasonHtml}
          </div>
        `;
        item.addEventListener("click", () => {
          this.$input.value = `聊聊「${p.title}」这件事`;
          this.send();
        });
        news.appendChild(item);
      });
      this.$messages.appendChild(wrap);
      this.$messages.scrollTop = this.$messages.scrollHeight;
    }

    appendTyping() {
      const node = document.createElement("div");
      node.className = "zmate-msg is-zmate";
      node.dataset.role = "typing";
      node.innerHTML = `
        <div class="zmate-msg__avatar">Z</div>
        <div class="zmate-msg__bubble"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>
      `;
      this.$messages.appendChild(node);
      this.$messages.scrollTop = this.$messages.scrollHeight;
      return node;
    }

    askNews() {
      this.open();
      if (this.streaming) return;
      // 点击「今日热点」只渲染 5 条热点卡片，不再追加任何对话气泡：
      // 既不写「我」的提问，也不再让 Zmate 跟一段「今日观察」。
      const typing = this.appendTyping();
      getJSON("/api/zmate/news")
        .then((data) => {
          typing.remove();
          if (data.picks && data.picks.length) {
            this.appendNewsCard(data.picks, {
              model_used: data.model_used,
              cache: data.cache,
            });
          } else {
            this.appendAssistant("今天的热榜暂时没有捞到内容，稍后再试一下吧。");
          }
        })
        .catch(() => {
          typing.remove();
          this.appendAssistant("热点接口暂时不可用，稍后再试一下吧。");
        });
    }

    send() {
      const text = this.$input.value.trim();
      if (!text || this.streaming) return;
      this.$input.value = "";
      this.autosize();
      this.appendUser(text);
      this.history.push({ role: "user", content: text });
      this.streamReply(text, {});
    }

    streamReply(_userText, { silent }) {
      this.streaming = true;
      this.$send.disabled = true;
      const typing = this.appendTyping();

      let assistantNode = null;
      let assistantBubble = null;
      let acc = "";

      const ensureNode = () => {
        if (!assistantNode) {
          typing.remove();
          assistantNode = this.appendAssistant("");
          assistantBubble = assistantNode.querySelector(".zmate-msg__bubble");
        }
      };

      this.abortCtl = new AbortController();
      const sel = this.selectedModel || {};
      streamPost("/api/zmate/chat", {
        messages: this.history,
        document: this.document
          ? {
              title: this.document.title,
              author: (this.document.author && this.document.author.name) || "",
              excerpt: this.document.excerpt || "",
              // 直答 / moonshot-v1-8k 在后端会切到「全文」分支，没传 paragraphs
              // 就退回老逻辑只用 excerpt，避免老页面调用断开。
              paragraphs: Array.isArray(this.document.paragraphs)
                ? this.document.paragraphs
                : [],
            }
          : null,
        context: silent ? "请基于刚刚提供的 TOP5 热点直接给出今日观察。" : "",
        provider: sel.provider || undefined,
        model: sel.model || undefined,
      }, {
        signal: this.abortCtl.signal,
        onDelta: (chunk) => {
          ensureNode();
          acc += chunk;
          assistantBubble.textContent = acc;
          this.$messages.scrollTop = this.$messages.scrollHeight;
        },
        onDone: () => {
          if (!assistantNode) {
            typing.remove();
            this.appendAssistant("（Zmate 没有产生有效回复，请稍后再试。）");
          } else {
            this.history.push({ role: "assistant", content: acc });
          }
          this.streaming = false;
          this.$send.disabled = !this.$input.value.trim();
        },
        onError: (err) => {
          if (typing.parentNode) typing.remove();
          this.appendAssistant("Zmate 暂时无法响应：" + err.message);
          this.streaming = false;
          this.$send.disabled = !this.$input.value.trim();
        },
      });
    }
  }

  global.Zmate = ZmateAssistant;
})(window);
