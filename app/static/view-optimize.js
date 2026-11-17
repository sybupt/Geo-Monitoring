/* 4 内容体检与改写：选目标模型（可多个），按会员套餐渠道生成投喂稿。
   每张稿卡可再次编辑（重算分数）与审核，审核通过才能进入引导投喂。 */
const OptimizeView = (() => {
  const st = { models: null, channels: null, result: null, membership: null, busy: false };

  function mount() {
    G.$("#op-run").addEventListener("click", run);
    G.$("#op-list").addEventListener("click", onListClick);
  }

  function onListClick(ev) {
    const approveBtn = ev.target.closest("[data-approve]");
    if (approveBtn) { approve(approveBtn.dataset.approve); return; }
    const editBtn = ev.target.closest("[data-edit]");
    if (editBtn) { toggleEdit(editBtn.dataset.edit); return; }
    const saveBtn = ev.target.closest("[data-save]");
    if (saveBtn) { saveEdit(saveBtn.dataset.save); return; }
    const copyBtn = ev.target.closest("[data-copy]");
    if (copyBtn) {
      const ta = G.$(`#doc-${copyBtn.dataset.copy}`);
      navigator.clipboard.writeText(ta.value).then(() => { copyBtn.textContent = "已复制"; });
    }
  }

  function allModels() { return (G.catalog ? G.catalog.models : []).map((m) => m.id); }

  function pickedModels() { return st.models || [allModels()[0]].filter(Boolean); }

  function pickedChannels() { return st.channels; }

  function renderControls() {
    // 模型多选
    const mSel = new Set(pickedModels());
    G.$("#op-models").innerHTML = (G.catalog ? G.catalog.models : []).map((m) => `
      <button class="chip-btn ${mSel.has(m.id) ? "on" : ""}" data-mid="${m.id}">
        ${G.esc(m.name)}</button>`).join("");
    G.$$("#op-models .chip-btn").forEach((b) => b.addEventListener("click", () => {
      const s = new Set(pickedModels());
      if (s.has(b.dataset.mid)) { if (s.size > 1) s.delete(b.dataset.mid); }
      else s.add(b.dataset.mid);
      st.models = [...s];
      renderControls();
    }));

    // 会员门控渠道
    const mem = st.membership;
    const allowed = mem ? mem.channels.map((c) => c.id) : [];
    const allowedSet = new Set(allowed);
    const sel = new Set(pickedChannels() || allowed);
    G.$("#op-channels").innerHTML = (G.catalog ? G.catalog.channels : []).map((c) => {
      const ok = allowedSet.has(c.id);
      return `<button class="chip-btn ${sel.has(c.id) ? "on" : ""}" data-cid="${c.id}"
        ${ok ? "" : 'disabled title="会员套餐未包含，需升级"'} style="${ok ? "" : "opacity:.4;cursor:not-allowed"}">
        ${G.esc(c.name)}${ok ? "" : " 🔒"}</button>`;
    }).join("");
    G.$$("#op-channels .chip-btn[data-cid]").forEach((b) => b.addEventListener("click", () => {
      const s = new Set(pickedChannels() || allowed);
      if (s.has(b.dataset.cid)) s.delete(b.dataset.cid); else s.add(b.dataset.cid);
      st.channels = [...s];
      renderControls();
    }));

    if (mem) {
      const locked = mem.locked_plans.map((p) =>
        `${G.planText(p)}：${p.channels.map((c) => c.name).join("、")}`).join("；");
      G.$("#op-plan").innerHTML =
        `当前套餐：<b>${G.esc(G.planText(mem))}</b> · 可投放 ${mem.channels.length} 个渠道` +
        (locked ? ` · 未开通：<span class="muted">${G.esc(locked)}</span>` : "");
    }
    G.$("#op-meta").textContent =
      `${pickedModels().length} 个模型 × ${(pickedChannels() || []).length} 个渠道 · 目标 ${(G.targetObj() || {}).title || "—"}`;
    G.$("#op-run").disabled = pickedModels().length === 0 || !(pickedChannels() || []).length;
  }

  function scoreBar(label, before, after, fmt) {
    const max = Math.max(before, after, 0.001);
    return `
      <div class="score-row">
        <span>${label}</span>
        <div class="dual">
          <div class="track"><div class="fill before" style="width:${(before / max) * 100}%"></div></div>
          <div class="track"><div class="fill after" style="width:${(after / max) * 100}%"></div></div>
        </div>
        <b>${fmt(before)} → ${fmt(after)}</b>
      </div>`;
  }

  function card(it) {
    const up = it.delta.score >= 0;
    const viol = (it.checks.violations || []);
    const approved = it.status === "approved";
    return `
    <div class="panel chan-card" id="card-${it.variant_id}">
      <div class="panel-head">
        <h2>${G.esc(it.channel_name)} <span class="tag">${G.esc(it.model_name)}</span>
          ${approved ? '<span class="st st-rechecked">已审核</span>' : '<span class="st st-ready">待审核</span>'}</h2>
        <span class="hint">${G.esc(it.style)}</span>
        <div style="flex:1"></div>
        <span class="score-pair">
          <em>${it.before.score.toFixed(1)}</em>
          <span>→</span>
          <b class="${up ? "up" : "down"}" id="score-${it.variant_id}">${it.after.score.toFixed(1)}</b>
          <i class="${up ? "up" : "down"}">${G.signed(it.delta.score, 1)}</i>
        </span>
      </div>
      <div class="panel-body">
        <div class="opt-grid">
          <div>
            <div class="sub-title">适配分拆解（同一把尺子）</div>
            ${scoreBar("GQS 内容质量", it.before.gqs, it.after.gqs, (v) => v.toFixed(1))}
            ${scoreBar("该格规则适配", it.before.rule_fit, it.after.rule_fit, (v) => v.toFixed(2))}
            ${scoreBar("问法覆盖", it.before.coverage, it.after.coverage, (v) => v.toFixed(2))}
            <div class="note" style="margin-top:6px">
              覆盖 ${it.questions} 条问法 · 规则来源：${it.rule_source === "learned" ? `该格已学 v${it.rule_version}` : "通用规则（建议先学该格）"}
            </div>
            <div class="sub-title">命中的规则权重</div>
            <div class="chips">${(it.applied_rules || []).map((r) =>
              `<span class="chip">${G.esc(r.label)} ${r.weight > 0 ? "+" : ""}${r.weight}</span>`).join("") || '<span class="note">—</span>'}</div>
            <div class="sub-title">硬约束校验</div>
            <div id="checks-${it.variant_id}">
              ${it.checks.passed
                ? '<div class="rule-note good">全部通过</div>'
                : `<div class="violations">${viol.map((v) => `<div class="viol ${v.level}"><b>${G.esc(v.detail)}</b><em>${G.esc(v.fix)}</em></div>`).join("")}</div>`}
            </div>
            <div class="note">字数 ${it.checks.stats.chars} · 外链 ${it.checks.stats.links} · 标题 ${it.checks.stats.title_len} 字</div>
            ${it.needs_translation ? '<div class="rule-note warn">英文渠道：当前只产出结构骨架，正文需走翻译环节（未接 LLM）</div>' : ""}
          </div>
          <div>
            <div class="doc-head">
              <b id="title-${it.variant_id}">${G.esc(it.title)}</b>
              <button class="btn ghost" data-copy="${it.variant_id}">复制正文</button>
            </div>
            <textarea class="wiz-input" id="doc-${it.variant_id}"
              style="min-height:260px;font:12px/1.7 var(--mono, monospace)"
              readonly>${G.esc(it.body)}</textarea>
            <div id="edit-${it.variant_id}" style="display:none;margin-top:8px">
              <label class="note">标题</label>
              <input type="text" class="wiz-input" id="etitle-${it.variant_id}"
                value="${G.esc(it.title)}" style="margin-top:4px">
              <div class="toolbar" style="margin-top:8px">
                <button class="btn" data-save="${it.variant_id}">保存并重算分数</button>
                <button class="btn ghost" data-edit="${it.variant_id}">取消</button>
              </div>
            </div>
            <div class="toolbar" style="margin-top:10px">
              <button class="btn ghost" data-edit="${it.variant_id}">再次编辑</button>
              <button class="btn" data-approve="${it.variant_id}" ${approved ? "disabled" : ""}>
                ${approved ? "已审核通过" : "审核通过"}</button>
            </div>
            <div class="note" id="apnote-${it.variant_id}"></div>
          </div>
        </div>
      </div>
    </div>`;
  }

  function toggleEdit(vid) {
    const box = G.$(`#edit-${vid}`);
    const ta = G.$(`#doc-${vid}`);
    const show = box.style.display === "none";
    box.style.display = show ? "block" : "none";
    ta.readOnly = !show;
  }

  async function saveEdit(vid) {
    const body = G.$(`#doc-${vid}`).value;
    const title = G.$(`#etitle-${vid}`).value.trim();
    if (!body.trim()) { G.toast("正文不能为空", true); return; }
    try {
      const d = await G.post(`/api/optimize/variant/${vid}`, { title, body });
      G.toast(`已重算：适配分 ${d.after.score}（编辑后回到待审核）`);
      const scoreEl = G.$(`#score-${vid}`);
      if (scoreEl) scoreEl.textContent = d.after.score.toFixed(1);
      const noteEl = G.$(`#apnote-${vid}`);
      if (noteEl) noteEl.textContent = d.note;
      const cardEl = G.$(`#card-${vid} .panel-head .st`);
      if (cardEl) { cardEl.className = "st st-ready"; cardEl.textContent = "待审核"; }
      const apBtn = G.$(`#card-${vid} [data-approve]`);
      if (apBtn) { apBtn.disabled = false; apBtn.textContent = "审核通过"; }
      // 更新约束展示
      const cbox = G.$(`#checks-${vid}`);
      if (cbox) {
        cbox.innerHTML = d.checks.passed
          ? '<div class="rule-note good">全部通过</div>'
          : `<div class="violations">${d.checks.violations.map((v) =>
              `<div class="viol ${v.level}"><b>${G.esc(v.detail)}</b><em>${G.esc(v.fix)}</em></div>`).join("")}</div>`;
      }
    } catch (e) {
      G.toast(`保存失败：${e.message}`, true);
    }
  }

  async function approve(vid) {
    try {
      const d = await G.post(`/api/optimize/variant/${vid}/approve`, {});
      G.toast("审核通过，可进入「引导投喂」");
      const cardEl = G.$(`#card-${vid} .panel-head .st`);
      if (cardEl) { cardEl.className = "st st-rechecked"; cardEl.textContent = "已审核"; }
      const btn = G.$(`#card-${vid} [data-approve]`);
      if (btn) { btn.disabled = true; btn.textContent = "已审核通过"; }
      const noteEl = G.$(`#apnote-${vid}`);
      if (noteEl) noteEl.textContent = d.note;
      G.loadProgress();
    } catch (e) {
      G.toast(`审核失败：${e.message}`, true);
    }
  }

  async function run() {
    if (st.busy) return;
    if (!(pickedChannels() || []).length) { G.toast("至少选一个渠道", true); return; }
    st.busy = true;
    const btn = G.$("#op-run");
    btn.disabled = true; btn.textContent = "生成中…";
    G.$("#op-list").innerHTML = '<div class="loading">按画像与规则改写，逐渠道套硬约束…</div>';
    try {
      const d = await G.post("/api/optimize", {
        target_id: G.state.target, model_ids: pickedModels(), channel_ids: pickedChannels(),
      });
      st.result = d;
      const pass = d.items.filter((x) => x.checks.passed).length;
      const cov = d.items.reduce((a, x) => a + x.after.coverage, 0) / d.items.length;
      G.$("#op-cards").innerHTML = [
        ["平均适配分提升", G.signed(d.avg_delta, 1), "分"],
        ["硬约束通过", `${pass}/${d.items.length}`, "渠道×模型"],
        ["改写后问法覆盖", G.pct(cov), "平均"],
        ["目标模型", d.models.map((m) => m.name).join("、"), ""],
        ["被套餐拦下", String(d.rejected.length), "渠道"],
      ].map(([l, v, u]) => `<div class="card"><div class="label">${l}</div>
        <div class="value">${v}${u ? `<small>${u}</small>` : ""}</div></div>`).join("");
      G.$("#op-list").innerHTML = d.items.map(card).join("");
      G.$("#op-note").textContent = d.note;
      G.loadProgress();
    } catch (e) {
      G.$("#op-list").innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
      G.toast(`生成失败：${e.message}`, true);
    } finally {
      st.busy = false;
      btn.disabled = false; btn.textContent = "生成投喂稿";
    }
  }

  async function loadMembership() {
    try {
      st.membership = await G.api(`/api/membership/${G.state.target}`);
    } catch (e) { st.membership = null; }
  }

  function refresh() {
    if (!G._mounted.optimize) return;
    loadMembership().then(() => renderControls());
    if (!st.result) {
      G.$("#op-list").innerHTML = `
        <div class="panel"><div class="empty" style="padding:44px 20px">
          选好目标模型与渠道后点「生成投喂稿」。<br>
          <span class="note">改写使用：目标画像（推荐流里那张卡）＋ 该格已学规则 ＋ 渠道硬约束。生成稿为「待审核」，可在线编辑与审核。</span>
        </div></div>`;
      if (new URLSearchParams(location.search).get("auto") === "opt") run();
      return;
    }
    if (st.result.target.item_id !== G.state.target) {
      st.result = null;
      G.$("#op-cards").innerHTML = "";
      G.$("#op-list").innerHTML = "";
    }
  }

  return { mount, refresh, run };
})();

G.register("optimize", OptimizeView);
