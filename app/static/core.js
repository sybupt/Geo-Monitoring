/* 共享内核：上下文、导航、请求、格式化。
   六个视图各自注册 mount/refresh，互相之间只通过 G.state 通信。 */

/* 前端错误可见化：脚本异常不再表现为"页面空白/模块打不开"，
   而是直接显示在右上角状态位，方便定位。 */
window.addEventListener("error", (e) => {
  const s = document.getElementById("status");
  if (s) {
    s.textContent = `脚本错误：${e.message || "未知"}`;
    s.style.color = "var(--bad)";
  }
});
window.addEventListener("unhandledrejection", (e) => {
  const s = document.getElementById("status");
  if (s && e.reason) {
    s.textContent = `请求失败：${e.reason.message || e.reason}`;
    s.style.color = "var(--bad)";
  }
});

const G = {
  state: {
    target: null,
    model: "deepseek_web",
    channel: "zhihu",
    cat: "",
    q: "",
    sort: "gqs_asc",
    optChannels: null,
  },
  views: {},
  catalog: null,
  modelMap: {},
  channelMap: {},
  targets: [],
  current: "rec",
  _mounted: {},
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

G.$ = $;
G.$$ = $$;

G.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

G.pct = (v, d = 0) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
G.num = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
G.money = (v) => `¥${Math.round(v).toLocaleString("zh-CN")}`;
G.signed = (v, d = 1, unit = "") => {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(d)}${unit}`;
};

G.api = async function (path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail
        : Array.isArray(j.detail) ? j.detail.map((d) => d.msg || d).join("; ")
          : JSON.stringify(j.detail || j);
    } catch (e) { /* 保留 statusText */ }
    throw new Error(msg);
  }
  return res.json();
};

G.post = function (path, body) {
  return G.api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
};

G.toast = function (msg, bad) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = bad ? "show bad" : "show";
  clearTimeout(G._toastT);
  G._toastT = setTimeout(() => { el.className = ""; }, 3200);
};

/* ---------------- 上下文 ---------------- */
G.save = function () {
  try {
    localStorage.setItem("georec.ctx", JSON.stringify({
      target: G.state.target, model: G.state.model, channel: G.state.channel,
    }));
  } catch (e) { /* 隐私模式忽略 */ }
};

G.restore = function () {
  try {
    const raw = localStorage.getItem("georec.ctx");
    if (!raw) return;
    const d = JSON.parse(raw);
    if (d.model) G.state.model = d.model;
    if (d.channel) G.state.channel = d.channel;
    if (d.target) G.state.target = d.target;
  } catch (e) { /* 忽略坏数据 */ }
};

G.targetObj = () => G.targets.find((t) => t.item_id === G.state.target) || null;
G.model = () => G.modelMap[G.state.model] || { id: G.state.model, name: G.state.model };
G.channel = () => G.channelMap[G.state.channel] || { id: G.state.channel, name: G.state.channel };

G.setCtx = function (patch) {
  Object.assign(G.state, patch);
  G.save();
  G.syncCtx();
  G.loadProgress();
  const v = G.views[G.current];
  if (v && v.refresh) v.refresh();
};

G.syncCtx = function () {
  const ts = $("#ctx-target");
  if (ts && G.state.target) ts.value = G.state.target;
  const ms = $("#ctx-model");
  if (ms) ms.value = G.state.model;
  const cs = $("#ctx-channel");
  if (cs) cs.value = G.state.channel;
};

G.reloadTargets = async function () {
  const data = await G.api("/api/targets?limit=200");
  G.targets = data.items;
  const ts = $("#ctx-target");
  if (!ts) return;
  ts.innerHTML = G.targets.map((t) => {
    const who = t.member_name || t.title.replace(t.category, "").trim();
    const what = t.promote_name && t.promote_name !== who ? ` · ${t.promote_name}` : "";
    return `<option value="${t.item_id}">${G.esc(who)}${G.esc(what)} · GQS ${t.gqs.toFixed(0)}</option>`;
  }).join("");
  if (!G.targets.some((t) => t.item_id === G.state.target)) {
    G.state.target = (G.targets[0] || {}).item_id || null;
  }

  // 顶部状态位：显示业务侧口径（企业会员 / 模型 / 渠道），
  // 不再展示语料库规模——那是检索对手，不是"目标物"，展示出来只会误导。
  const st = $("#status");
  if (st) {
    st.textContent = `${G.targets.length} 家企业会员 · ${G.catalog.models.length} 模型 · ${G.catalog.channels.length} 渠道`;
    st.title = "企业会员数来自种子清单；模型与渠道数决定规则矩阵的格子数";
  }
  G.syncCtx();
  G.loadProgress();
};

G.loadProgress = async function () {
  const box = $("#ctx-progress");
  if (!box) return;
  if (!G.state.target) { box.innerHTML = ""; return; }
  try {
    const [p, mem] = await Promise.all([
      G.api(`/api/targets/${G.state.target}/progress?model_id=${G.state.model}`),
      G.api(`/api/membership/${G.state.target}`).catch(() => null),
    ]);
    const chTotal = mem ? mem.channels.length : 6;
    const steps = [
      ["目标已选", true, "当前目标物已选定", "rec"],
      ["问题集", p.question_set_dims > 0, `${p.question_set_dims} 个维度`, "questions"],
      ["基线", p.baseline_models > 0, `${p.baseline_models}/${G.catalog ? G.catalog.models.length : 6} 模型`, "questions"],
      ["规则", p.learned_models >= (G.catalog ? G.catalog.models.length : 6),
        `${p.learned_models} 模型已学`, "rules"],
      ["改写", p.approved_channels > 0, `${p.approved_channels}/${chTotal} 渠道过审`, "optimize"],
      ["投喂", p.feed_channels > 0, `${p.feed_channels} 渠道`, "feed"],
      ["复测", p.recheck_channels > 0, `${p.recheck_channels} 渠道`, "board"],
    ];
    box.innerHTML = steps.map(([label, done, extra, view]) => `
      <button type="button" class="pstep ${done ? "done" : ""} ${G.current === view ? "current" : ""}"
              data-goto="${view}" title="${G.esc(extra || label)}（点击跳转）">
        <i>${done ? "✓" : "○"}</i>${G.esc(label)}
      </button>`).join("");
    $$("#ctx-progress .pstep[data-goto]").forEach((el) => {
      el.addEventListener("click", () => G.open(el.dataset.goto));
    });
  } catch (e) {
    box.innerHTML = "";
  }
};

/* ---------------- 导航 ---------------- */
const PAGE_META = {
  members: ["会员管理", "生态位台账：管理企业主体、推广对象（品牌/产品）、套餐等级与投放渠道权限"],
  rec: ["推荐流 · 生态位总览", "盘点企业真实力、内容质量分（GQS）与各模型×渠道下的竞争现状"],
  questions: ["测试问题集 · 需求镜像", "按行业生成多维度真实用户问法，一键测量全模型提及率基线"],
  rules: ["规则与矩阵 · 模型偏好", "6 模型 × 11 渠道规则权重学习（AUC）与 AutoGEO 可执行检查器"],
  optimize: ["内容体检与改写 · 实力显影", "结合目标画像、已学规则与渠道硬约束，生成并审核高适配投喂稿"],
  feed: ["引导投喂 · 有意义投喂", "按权威页→社区扩散排期执行投放，登记回填链接与时长并触发复测"],
  board: ["效果看板 · 生态位复测", "同一批问法投喂前后提及率对比、z 检验显著性归因与趋势追踪"],
};

G.open = function (name) {
  G.current = name;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  $$("#ctx-progress .pstep").forEach((p) => p.classList.toggle("current", p.dataset.goto === name));
  const meta = PAGE_META[name];
  if (meta) {
    const t = $("#page-title"), s = $("#page-sub");
    if (t) t.textContent = meta[0];
    if (s) s.textContent = meta[1];
  }
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  const v = G.views[name];
  if (!v) return;
  if (!G._mounted[name]) {
    G._mounted[name] = true;
    try { v.mount(); } catch (e) { console.error(e); }
  }
  if (v.refresh) v.refresh();
};

G.register = function (name, mod) { G.views[name] = mod; };

/* ---------------- 小部件 ---------------- */
G.scoreBadge = (score, low = 55) => {
  const n = Number(score);
  const cls = n >= 85 ? "A" : n >= 70 ? "B" : n >= low ? "C" : "D";
  return `<span class="gqs-badge g-${cls}" title="GQS 内容质量分 ${n.toFixed(1)}（${cls} 级）"><em>${cls}</em>${n.toFixed(0)}</span>`;
};

G.heatStyle = (ratio) => {
  if (ratio === null || ratio === undefined || Number.isNaN(Number(ratio))) return "";
  const r = Math.max(0, Math.min(1, Number(ratio)));
  const alpha = (0.04 + r * 0.24).toFixed(3);
  return `background:rgba(15,110,82,${alpha});`;
};

G.bar = (v, cls = "") => `
  <div class="track"><div class="fill ${cls}" style="width:${Math.max(2, Math.min(100, v * 100))}%"></div></div>`;

G.spark = (runs) => {
  if (!runs || !runs.length) return '<span class="note">—</span>';
  const vals = runs.map((r) => r.hit_rate);
  const max = Math.max(...vals, 0.01);
  return `<span class="spark">${runs.map((r) => `
    <i class="${r.phase}" style="height:${Math.max(4, (r.hit_rate / max) * 22)}px"
       title="${G.esc(r.date)} ${G.esc(r.phase)} ${G.pct(r.hit_rate)}"></i>`).join("")}</span>`;
};

/* 套餐文案：试用档价格就叫"免费"，标签里已含，别再拼一次 */
G.planText = (p) => {
  if (!p) return "试用（免费）";
  const label = p.plan_label || p.label || "";
  const price = p.price || "";
  return price && label && !label.includes(price) ? `${label}（${price}）` : (label || price);
};

G.statusPill = (status, label) => `<span class="st st-${status}">${G.esc(label)}</span>`;

/* ---------------- 启动 ---------------- */
G.boot = async function () {
  try {
    await G.api("/api/health");
    $("#status").textContent = "服务正常";
  } catch (e) {
    $("#status").textContent = "服务异常";
  }
  try {
    G.catalog = await G.api("/api/catalog");
    G.catalog.models.forEach((m) => { G.modelMap[m.id] = m; });
    G.catalog.channels.forEach((c) => { G.channelMap[c.id] = c; });
  } catch (e) {
    G.toast(`目录加载失败：${e.message}`, true);
    return;
  }

  G.restore();
  const data = await G.api("/api/targets?limit=200");
  G.targets = data.items;
  const ts = $("#ctx-target");
  ts.innerHTML = G.targets.map((t) => {
    // 顶部按企业名展示，后面跟推广对象，避免把品牌/产品当成客户
    const who = t.member_name || t.title.replace(t.category, "").trim();
    const what = t.promote_name && t.promote_name !== who ? ` · ${t.promote_name}` : "";
    return `<option value="${t.item_id}">${G.esc(who)}${G.esc(what)} · GQS ${t.gqs.toFixed(0)}</option>`;
  }).join("");
  if (!G.targets.some((t) => t.item_id === G.state.target)) {
    G.state.target = (G.targets[0] || {}).item_id || null;
  }

  // 顶部状态位：显示业务侧口径（企业会员 / 模型 / 渠道），
  // 不再展示语料库规模——那是检索对手，不是"目标物"，展示出来只会误导。
  const st = $("#status");
  if (st) {
    st.textContent = `${G.targets.length} 家企业会员 · ${G.catalog.models.length} 模型 · ${G.catalog.channels.length} 渠道`;
    st.title = "企业会员数来自种子清单；模型与渠道数决定规则矩阵的格子数";
  }

  // 全局条只保留目标物；模型/渠道在各自页面内选择：
  // 第 3 步点矩阵格，第 4 步页内多选，第 5 步逐行自带，第 6 步页内切换。
  ts.addEventListener("change", () => G.setCtx({ target: ts.value }));

  $$("#tabs button").forEach((b) => b.addEventListener("click", () => G.open(b.dataset.view)));

  G.syncCtx();
  G.loadProgress();
  const hash = (location.hash || "").replace("#", "");
  G.open(["rec", "questions", "rules", "optimize", "feed", "board", "members"].includes(hash) ? hash : "members");

  // 深链与验收钩子：?item=I0005 直开目标详情；?auto=opt 自动改写一轮
  const params = new URLSearchParams(location.search);
  const item = params.get("item");
  if (item && G.targets.some((t) => t.item_id === item)) {
    setTimeout(() => G.views.rec.openDetail(item), 80);
  }
};

document.addEventListener("DOMContentLoaded", G.boot);
