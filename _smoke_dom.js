/* 极简 DOM 环境，用于在没有浏览器的情况下跑一遍前端脚本，
   捕获运行时异常并检查各视图是否真的渲染出内容。 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const ROOT = "D:/projects/georec/app/static";
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

class El {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this._html = "";
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.className = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this._listeners = {};
    const self = this;
    this.classList = {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); self.className = [...this._s].join(" "); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); self.className = [...this._s].join(" "); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) {
        const want = on === undefined ? !this._s.has(c) : !!on;
        want ? this._s.add(c) : this._s.delete(c);
        self.className = [...this._s].join(" ");
        return want;
      },
    };
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
  removeEventListener() {}
  appendChild(c) { this.children.push(c); return c; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  focus() {}
  click() { (this._listeners.click || []).forEach((f) => f({ target: this })); }
  closest() { return this; }
  getAttribute() { return null; }
  setAttribute() {}
}

const ids = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
const byId = {};
ids.forEach((id) => { byId[id] = new El("div", id); });

// 页签
const tabViews = ["rec", "questions", "rules", "optimize", "feed", "board", "members"];
const tabBtns = tabViews.map((v) => { const b = new El("button"); b.dataset.view = v; return b; });

const docHandlers = [];
const winHandlers = {};
const document = {
  getElementById: (id) => byId[id] || null,
  querySelector: (sel) => {
    if (sel.startsWith("#")) return byId[sel.slice(1).split(/[ .]/)[0]] || null;
    return null;
  },
  querySelectorAll: (sel) => {
    if (sel === "#tabs button") return tabBtns;
    return [];
  },
  createElement: (tag) => new El(tag),
  addEventListener: (t, fn) => { if (t === "DOMContentLoaded") docHandlers.push(fn); },
  body: new El("body"),
};

const errors = [];
process.on("unhandledRejection", (e) => errors.push(`[reject] ${e && e.stack || e}`));
process.on("uncaughtException", (e) => { errors.push(`[uncaught] ${e.stack}`); });
const window = {
  addEventListener: (t, fn) => { (winHandlers[t] = winHandlers[t] || []).push(fn); },
  location: { hash: "", search: "", href: "http://127.0.0.1:8000/" },
  localStorage: {
    _d: {},
    getItem(k) { return this._d[k] ?? null; },
    setItem(k, v) { this._d[k] = String(v); },
  },
  history: { replaceState() {} },
  setTimeout, clearTimeout, console,
  URLSearchParams,
  SpeechRecognition: undefined,
  webkitSpeechRecognition: undefined,
};

const sandbox = {
  window, document, console, setTimeout, clearTimeout,
  localStorage: window.localStorage,
  location: window.location,
  history: window.history,
  URLSearchParams,
  fetch: (url, opts) => fetch(`http://127.0.0.1:8000${url}`, opts),
  process, require,
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

const files = ["core.js", "chat.js", "view-targets.js", "view-questions.js",
  "view-rules.js", "view-optimize.js", "view-feed.js", "view-board.js", "view-members.js"];
for (const f of files) {
  const code = fs.readFileSync(path.join(ROOT, f), "utf8");
  try {
    vm.runInContext(code, sandbox, { filename: f });
  } catch (e) {
    errors.push(`[load] ${f}: ${e.message}`);
  }
}

(async () => {
  // 触发 DOMContentLoaded（core.boot + chat.mount）
  for (const fn of docHandlers) {
    try { fn(); } catch (e) { errors.push(`[DOMContentLoaded] ${e.stack}`); }
  }
  await new Promise((r) => setTimeout(r, 2500));

  let G; try { G = vm.runInContext("G", sandbox); } catch (e) { G = null; }
  if (!G) { console.log("G 未定义", errors); process.exit(1); }
  console.log("目标物数量:", G.targets.length, "| 模型:", (G.catalog || {}).models?.length);
  console.log("ctx-target options 长度:", byId["ctx-target"].innerHTML.length);

  const viewBox = { rec: "rec-grid", questions: "qz-list", rules: "rl-grid",
    optimize: "op-models", feed: "fd-sched", board: "bd-cards", members: "mb-table" };
  for (const name of tabViews) {
    try {
      G.open(name);
      await new Promise((r) => setTimeout(r, 900));
      const box = byId[viewBox[name]];
      const len = box ? box.innerHTML.length : -1;
      console.log(`视图 ${name}: 容器内容长度 ${len} ${len > 40 ? "OK" : "⚠ 空"}`);
    } catch (e) {
      errors.push(`[view ${name}] ${e.stack}`);
    }
  }

  // 对话窗口：默认应为隐藏
  console.log("chat-win hidden:", byId["chat-win"].hidden);
  try {
    byId["chat-fab"].click();
    console.log("打开对话窗后 hidden:", byId["chat-win"].hidden,
      "| 气泡数:", byId["chat-msgs"].children.length);
    byId["chat-text"].value = "重点出价格和避坑的问题";
    const sendBtn = byId["chat-send"];
    await new Promise((r) => setTimeout(r, 1500));
    console.log("对话发送后气泡数:", byId["chat-msgs"].children.length);
  } catch (e) {
    errors.push(`[chat] ${e.stack}`);
  }

  if (errors.length) {
    console.log("\n=== 运行时错误 ===");
    errors.forEach((e) => console.log(e.split("\n").slice(0, 3).join(" | ")));
  } else {
    console.log("\n无运行时错误");
  }
})();
