/* 5 引导投喂：把审核通过的改写稿按渠道逐一投放。
   登记时回填链接、投放时长与目标模型；工作台模式，不代替人工发布。 */
const FeedView = (() => {
  const st = { plan: null, recheck: {}, busy: {}, modelPick: {}, editing: {}, draft: {} };

  function mount() {
    const list = G.$("#fd-list");
    list.addEventListener("click", (ev) => {
      const rec = ev.target.closest("[data-record]");
      if (rec) { record(rec.dataset.record, rec.dataset.vid, rec.dataset.mid, rec.dataset.edit === "1"); return; }
      const em = ev.target.closest("[data-editmode]");
      if (em) { st.editing[em.dataset.editmode] = true; renderFeeds(); return; }
      const ce = ev.target.closest("[data-canceledit]");
      if (ce) { delete st.editing[ce.dataset.canceledit]; delete st.draft[ce.dataset.canceledit]; renderFeeds(); return; }
      const rc = ev.target.closest("[data-recheck]");
      if (rc) { recheck(rc.dataset.recheck, rc.dataset.mid); return; }
      const pv = ev.target.closest("[data-preview]");
      if (pv) { openPreview(pv.dataset.preview, pv.dataset.mid); return; }
      const tg = ev.target.closest("[data-toggle]");
      if (tg) {
        const box = G.$(`#rc-detail-${tg.dataset.toggle}`);
        if (box) box.style.display = box.style.display === "none" ? "block" : "none";
      }
    });
    list.addEventListener("change", (ev) => {
      const sel = ev.target.closest("[data-modelpick]");
      if (sel) {
        st.modelPick[sel.dataset.modelpick] = sel.value;
        saveDraft(sel.dataset.modelpick);
        renderFeeds();
      }
    });
    list.addEventListener("input", (ev) => {
      const box = ev.target.closest("[data-draftfor]");
      if (box) saveDraft(box.dataset.draftfor);
    });
  }

  // 编辑草稿：切模型重渲染时不丢用户已输入的链接/时长
  function saveDraft(ch) {
    const url = (G.$(`#url-${ch}`) || {}).value;
    const dur = (G.$(`#dur-${ch}`) || {}).value;
    st.draft[ch] = { url: url ?? "", dur: dur ?? "" };
  }

  async function load() {
    const box = G.$("#fd-sched");
    box.innerHTML = '<div class="loading">加载作业单…</div>';
    try {
      const d = await G.api(`/api/feed/plan?target_id=${G.state.target}`);
      st.plan = d;
      render();
    } catch (e) {
      box.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  function render() {
    const d = st.plan;
    if (!d) return;
    const mem = d.membership;
    G.$("#fd-sched").innerHTML = `
      <div class="timeline">
        ${d.schedule.map((s) => `
          <div class="tl-step ${s.done ? "done" : ""}">
            <i>${s.done ? "✓" : s.step}</i>
            <div>
              <b>${G.esc(s.channel_name)}</b>
              <span>${G.esc(s.why)}</span>
            </div>
          </div>`).join("")}
      </div>
      <div class="note" style="margin-top:12px">
        会员套餐：<b>${G.esc(G.planText(mem))}</b> · 可投放 ${mem.channels.length} 个渠道 ·
        已投喂 ${d.fed}/${d.total} 个渠道
      </div>
      <div class="rule-note" style="margin-top:6px">${G.esc(d.note)}</div>`;

    // 只列套餐内的渠道：套餐外的渠道在接口层就过滤掉，界面保持干净
    G.$("#fd-list").innerHTML = d.items.length
      ? d.items.map(card).join("")
      : '<div class="empty">该会员套餐暂无可投放渠道</div>';
  }

  function card(it) {
    const g = it.guide;
    const approved = it.approved || [];
    const pickedModel = st.modelPick[it.channel_id] || (approved[0] && approved[0].model_id) || "";
    const picked = approved.find((a) => a.model_id === pickedModel) || approved[0];
    const busy = st.busy[it.channel_id];
    const fed = !!it.feed;
    const editing = fed && !!st.editing[it.channel_id];
    const locked = fed && !editing;   // 已登记未进编辑态：链接/天数/模型全部锁定
    const draft = st.draft[it.channel_id] || {};
    const urlVal = draft.url !== undefined && draft.url !== "" ? draft.url
      : (it.feed && it.feed.url) || "";
    const durVal = draft.dur || "30";
    // 按钮状态机：未登记 → 只能登记；已登记锁定 → 只能复测或进编辑；编辑中 → 只能再次登记/取消
    const actions = !fed ? `
      <button class="btn" data-record="${it.channel_id}" data-vid="${(picked || {}).variant_id || ""}"
        data-mid="${(picked || {}).model_id || ""}" ${busy ? "disabled" : ""}>${busy ? "登记中…" : "登记投喂"}</button>
      <button class="btn ghost" disabled title="先登记投喂，再复测">投喂后复测</button>
    ` : editing ? `
      <button class="btn solid" data-record="${it.channel_id}" data-edit="1"
        data-vid="${(picked || {}).variant_id || ""}" data-mid="${(picked || {}).model_id || ""}"
        ${busy ? "disabled" : ""}>${busy ? "登记中…" : "再次登记"}</button>
      <button class="btn ghost" data-canceledit="${it.channel_id}" ${busy ? "disabled" : ""}>取消</button>
      <button class="btn ghost" disabled title="确认「再次登记」后才能复测">投喂后复测</button>
    ` : `
      <button class="btn" disabled>已登记 ✓</button>
      <button class="btn ghost" data-editmode="${it.channel_id}">登记修改</button>
      <button class="btn ghost" data-recheck="${it.channel_id}" data-mid="${(picked || {}).model_id || ""}"
        ${busy ? "disabled" : ""}>${busy ? "复测中…" : "投喂后复测"}</button>
    `;
    return `
    <div class="feed-card">
      <div class="fc-head">
        <div>
          <b>${G.esc(it.channel_name)}</b>
          ${G.statusPill(it.status, it.status_label)}
          <span class="note">${g.min_words}–${g.max_words} 字 · 外链 ≤ ${g.max_links}</span>
        </div>
        <div class="fc-meta">
          <span class="tag">${approved.length} 个模型的审核稿</span>
        </div>
      </div>
      <div class="fc-body">
        <div class="fc-guide">
          <div class="kv"><span>发布位置</span><b>${G.esc(g.placement)}</b></div>
          <div class="kv"><span>标题写法</span><b>${G.esc(g.title)}</b></div>
          <div class="kv"><span>首段要求</span><b>${G.esc(g.lead)}</b></div>
          <div class="kv"><span>时间窗</span><b>${G.esc(g.timing)}</b></div>
          <div class="kv"><span>外链策略</span><b>${G.esc(g.links)}</b></div>
          <div class="sub-title">审核红线</div>
          <div class="chips">${g.review.map((x) => `<span class="chip bad">${G.esc(x)}</span>`).join("")}</div>
        </div>
        <div class="fc-act">
          ${approved.length ? `
            <div class="sub-title">选择本次投喂针对的模型</div>
            <select class="wiz-input" data-modelpick="${it.channel_id}" style="max-width:260px" ${locked ? "disabled" : ""}>
              ${approved.map((a) => `<option value="${a.model_id}" ${a.model_id === (picked || {}).model_id ? "selected" : ""}>
                ${G.esc(a.model_name)} · 适配分 ${a.score === null ? "—" : Number(a.score).toFixed(1)}</option>`).join("")}
            </select>
            ${picked ? `
              <div class="fc-vhead">
                <div>
                  <div class="fc-version">${G.esc(picked.title)}</div>
                  <div class="note">审核于 ${G.esc(picked.at || "")}</div>
                </div>
                <button class="btn ghost" type="button" data-preview="${it.channel_id}" data-mid="${G.esc(picked.model_id)}">预览全文</button>
              </div>` : ""}
            <div class="toolbar" style="margin-top:8px;align-items:center;gap:8px" data-draftfor="${it.channel_id}">
              <input type="text" class="url-input" id="url-${it.channel_id}" style="margin-top:0"
                placeholder="发布后回填链接，例如 https://…" value="${G.esc(urlVal)}" ${locked ? "disabled" : ""}>
              <input type="number" id="dur-${it.channel_id}" min="1" max="365" value="${G.esc(durVal)}"
                title="投放时长（天）" style="width:88px;padding:6px 8px;font-size:12.5px" ${locked ? "disabled" : ""}>
              <span class="note">天</span>
            </div>
            <div class="toolbar" style="margin:10px 0 0">${actions}</div>
            <div class="note">${editing ? "修改中：确认无误后点「再次登记」，生成新记录、旧记录保留可追溯"
              : fed ? "已登记，链接/时长已锁定；填错了点「登记修改」"
              : "登记会记录：链接 · 投喂时间 · 投放时长 · 针对模型 · 投喂评分"}</div>
          ` : '<div class="rule-note warn">该渠道还没有「审核通过」的改写稿，先去「内容体检与改写」生成并审核</div>'}
          <div id="rc-${it.channel_id}">${st.recheck[it.channel_id] ? recheckHtml(it.channel_id, st.recheck[it.channel_id]) : ""}</div>
        </div>
      </div>
    </div>`;
  }

  function recheckHtml(ch, d) {
    const up = d.delta >= 0;
    const cls = d.level === "significant" ? (up ? "good" : "bad") : "warn";
    return `
      <div class="rc-box">
        <div class="stat-row" style="margin:10px 0 6px">
          <div class="stat"><span>原稿引用率</span><b>${G.pct(d.before.hit_rate)}</b><em>${d.before.hits}/${d.before.samples}</em></div>
          <div class="stat"><span>投喂稿引用率</span><b class="${cls}">${G.pct(d.after.hit_rate)}</b><em>${d.after.hits}/${d.after.samples}</em></div>
          <div class="stat"><span>相对变化</span><b class="${up ? "good" : "bad"}">${G.signed(d.delta * 100, 1, "pt")}</b><em>z = ${d.z}</em></div>
        </div>
        <div class="rule-note ${cls}">${G.esc(d.verdict)}</div>
        <button class="btn ghost" data-toggle="${ch}" style="padding:4px 10px;font-size:12px">
          查看 ${d.detail.length} 条问法明细</button>
        <div id="rc-detail-${ch}" style="display:none;margin-top:8px">
          <table class="probe-table"><thead><tr><th>问法</th><th>投喂后</th></tr></thead>
          <tbody>${d.detail.map((r) => `<tr>
            <td>${G.esc(r.query)}</td>
            <td>${r.cited ? "#" + r.rank : "未引用"}</td>
          </tr>`).join("")}</tbody></table>
        </div>
      </div>`;
  }

  async function record(ch, vid, mid, edit) {
    const url = (G.$(`#url-${ch}`) || {}).value || "";
    const dur = parseInt((G.$(`#dur-${ch}`) || {}).value || "30", 10);
    st.busy[ch] = true; render();
    try {
      await G.post("/api/feed/record", {
        target_id: G.state.target, model_id: mid || G.state.model, channel_id: ch,
        variant_id: vid ? parseInt(vid, 10) : null, url: url.trim(), duration_days: dur,
      });
      G.toast(edit ? "已更新登记（生成新记录，旧记录保留可追溯）"
                   : "已登记投喂（链接/时间/时长/模型已记录）");
      delete st.editing[ch];
      delete st.draft[ch];
      await load();
      G.loadProgress();
    } catch (e) {
      G.toast(`登记失败：${e.message}`, true);
    } finally {
      st.busy[ch] = false; render();
    }
  }

  async function recheck(ch, mid) {
    st.busy[ch] = true; render();
    try {
      const d = await G.post("/api/feed/recheck", {
        target_id: G.state.target, model_id: mid || G.state.model, channel_id: ch,
      });
      st.recheck[ch] = d;
      G.toast(`复测完成：${G.pct(d.before.hit_rate)} → ${G.pct(d.after.hit_rate)}（z=${d.z}）`);
      await load();
      G.loadProgress();
    } catch (e) {
      G.toast(`复测失败：${e.message}`, true);
    } finally {
      st.busy[ch] = false; render();
    }
  }

  function openPreview(ch, mid) {
    if (!st.plan) return;
    const it = st.plan.items.find((x) => x.channel_id === ch);
    if (!it) return;
    const v = (it.approved || []).find((a) => a.model_id === mid) || (it.approved || [])[0];
    if (!v) return;
    G.openPreview({
      title: v.title,
      body: v.body || "",
      channel: it.channel_name,
      model: v.model_name,
      score: v.score,
      checks: v.checks,
    });
  }

  function renderFeeds() {
    G.$("#fd-list").innerHTML = st.plan.items.map(card).join("");
  }

  function refresh() {
    if (!G._mounted.feed) return;
    st.recheck = {};
    st.editing = {};
    st.draft = {};
    load();
  }

  return { mount, refresh };
})();

G.register("feed", FeedView);
