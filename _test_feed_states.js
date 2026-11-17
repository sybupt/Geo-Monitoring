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
  
  // ---- 投喂卡片三态状态机验证 ----
  const t = (name, ok) => console.log(ok ? "PASS" : "FAIL", name);
  G.open("feed");
  await new Promise((r) => setTimeout(r, 1200));
  // 让 feed 视图用 E001 渲染
  G.state.target = "E001"; G.state.model = "deepseek_web";
  await G.views.feed.refresh();
  await new Promise((r) => setTimeout(r, 1200));
  const h1 = byId["fd-list"].innerHTML;
  const ownIdx = h1.indexOf('id="url-own_blog"');
  const own = ownIdx < 0 ? "" : h1.slice(Math.max(0, ownIdx - 3500), ownIdx + 1600);
  t("锁定态: 已登记 ✓", own.includes("已登记 ✓"));
  t("锁定态: 出现登记修改", own.includes("登记修改"));
  t("锁定态: 不出现再次登记", !own.includes("再次登记"));
  t("锁定态: 链接框 disabled", new RegExp('id="url-own_blog"[^>]*disabled').test(h1));
  // 点击「登记修改」→ 编辑态
  const em = h1.match(/data-editmode="own_blog"/);
  if (em) {
    (byId["fd-list"]._listeners.click || []).forEach((f) => f({ target: { closest: (s) => s === "[data-editmode]" ? { dataset: { editmode: "own_blog" } } : null } }));
    const h2 = byId["fd-list"].innerHTML;
    const own2 = h2.slice(Math.max(0, h2.indexOf('id="url-own_blog"') - 3500), h2.indexOf('id="url-own_blog"') + 1600);
    t("编辑态: 再次登记(绿色 solid)", own2.includes("btn solid") && own2.includes("再次登记"));
    t("编辑态: 出现取消", own2.includes("取消"));
    t("编辑态: 链接框未锁", !new RegExp('id="url-own_blog"[^>]*disabled').test(h2));
    t("编辑态: 登记修改消失", !own2.includes("登记修改"));
    (byId["fd-list"]._listeners.click || []).forEach((f) => f({ target: { closest: (s) => s === "[data-canceledit]" ? { dataset: { canceledit: "own_blog" } } : null } }));
    const h3 = byId["fd-list"].innerHTML;
    const own3 = h3.slice(Math.max(0, h3.indexOf('id="url-own_blog"') - 3500), h3.indexOf('id="url-own_blog"') + 1600);
    t("取消后: 回到锁定态", own3.includes("已登记 ✓") && !own3.includes("再次登记"));
  } else { t("找不到 own_blog 的登记修改按钮", false); }
  // 未登记渠道（trial 会员或无 feed 的渠道）
  const notFed = h1.split('feed-card"').find(s => s.includes(">登记投喂<"));
  t("未登记态: 存在登记投喂", !!notFed);
  t("未登记态: 复测禁用", !!(notFed && notFed.includes("先登记投喂，再复测")));
  console.log("---- 三态验证结束 ----");

  if (errors.length) {
    console.log("\n=== 运行时错误 ===");
    errors.forEach((e) => console.log(e.split("\n").slice(0, 3).join(" | ")));
  } else {
    console.log("\n无运行时错误");
  }
})();
