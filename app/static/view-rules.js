/* 3 规则与矩阵：AutoGEO 规则的可执行化 + 36 格规则学习。
   矩阵在上（哪一格学了多少），规则卡在下（这一格具体偏爱什么）。 */
const RulesView = (() => {
  const st = { cells: [], detail: null, busy: false };

  function mount() {
    G.$("#rl-learn-cell").addEventListener("click", () => learnOne());
    G.$("#rl-learn-all").addEventListener("click", () => learnAll());
  }

  async function loadChecks() {
    const box = G.$("#rl-checks");
    try {
      const d = await G.api("/api/rules");
      box.innerHTML = `
        <table>
          <thead><tr><th>规则</th><th>可执行判据</th><th class="num">权重</th><th class="num">平均分</th><th class="num">达标率</th><th class="num">失守率</th></tr></thead>
          <tbody>${d.rules.map((r) => `<tr>
            <td><b>${G.esc(r.label)}</b><div class="note">${G.esc(r.key)}</div></td>
            <td class="desc">${G.esc(r.desc)}</td>
            <td class="num">${r.weight.toFixed(2)}</td>
            <td class="num"><div class="cell-bar"><span>${r.avg_score.toFixed(2)}</span>${G.bar(r.avg_score)}</div></td>
            <td class="num"><div class="cell-bar"><span>${G.pct(r.pass_rate)}</span>${G.bar(r.pass_rate)}</div></td>
            <td class="num" style="color:${r.fail_rate > 0.4 ? "var(--warn)" : "inherit"}">${G.pct(r.fail_rate)}</td>
          </tr>`).join("")}</tbody>
        </table>
        <div class="note" style="margin-top:12px">
          ${d.items} 个目标物 · 平均 GQS ${d.avg_gqs} · 低于 55 分 ${d.low_gqs_items} 个。
          达标 = 该项得分 ≥ 0.75；失守 = < 0.5。权重是规则集合的归一化，不是学习结果。
        </div>`;
    } catch (e) {
      box.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  async function loadMatrix() {
    try {
      const d = await G.api("/api/mx/matrix");
      st.cells = d.cells;
      G.$("#rl-meta").textContent = `${d.learned_count}/${d.total} 格已学 · 当前 ${G.model().name} × ${G.channel().name}`;
      renderGrid();
    } catch (e) {
      G.$("#rl-grid").innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  async function loadPref() {
    const box = G.$("#rl-pref");
    try {
      const d = await G.api(`/api/questions/baseline_matrix?target_id=${G.state.target}`);
      if (!d.models.length) { box.innerHTML = '<div class="empty">先去「测试问题集」测一轮全模型基线</div>'; return; }
      const dims = d.dimensions;
      const label = (dim) => { const s = st._dimLabels; return (s && s[dim]) || dim; };
      box.innerHTML = `<div class="tgrid">${G.catalog.models.filter((m) => d.models.includes(m.id)).map((m) => {
        const vals = dims.map((dim) => ({ dim, rate: (d.matrix[`${m.id}::${dim}`] || {}).hit_rate }))
          .filter((x) => x.rate !== undefined)
          .sort((a, b) => b.rate - a.rate);
        if (!vals.length) return "";
        const best = vals.slice(0, 2), worst = vals.slice(-2);
        const chip = (x, cls) => `<span class="chip ${cls}">${G.esc(label(x.dim))} ${G.pct(x.rate)}</span>`;
        return `<div class="tcard" style="cursor:default">
          <div class="tc-head"><div><div class="tc-title">${G.esc(m.name)}</div>
            <div class="tc-sub">${G.esc(m.vendor)} · ${vals.length} 个维度</div></div>
            <b>${G.pct(vals.reduce((a, x) => a + x.rate, 0) / vals.length)}</b></div>
          <div class="sub-title">最容易引用的维度</div>
          <div class="chips">${best.map((x) => chip(x, "geo")).join("")}</div>
          <div class="sub-title">最难挤进去的维度</div>
          <div class="chips">${worst.map((x) => chip(x, "")).join("")}</div>
        </div>`;
      }).join("")}</div>`;
    } catch (e) {
      box.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  function renderGrid() {
    const models = G.catalog.models;
    const channels = G.catalog.channels;
    const by = {};
    st.cells.forEach((c) => { by[`${c.model_id}::${c.channel_id}`] = c; });
    let html = `<table class="mx-table"><thead><tr><th class="corner">模型 \\ 渠道</th>`;
    channels.forEach((c) => { html += `<th>${G.esc(c.name)}</th>`; });
    html += "</tr></thead><tbody>";
    models.forEach((m) => {
      html += `<tr><th class="row-head"><span>${G.esc(m.name)}</span><em>${G.esc(m.vendor)}</em></th>`;
      channels.forEach((c) => {
        const cell = by[`${m.id}::${c.id}`] || {};
        const on = cell.learned;
        const active = m.id === G.state.model && c.id === G.state.channel ? " active" : "";
        const auc = on ? Number(cell.auc) : 0;
        const norm = on ? Math.max(0, Math.min(1, (auc - 0.5) / 0.45)) : 0;
        const heat = on ? `style="background:rgba(15,110,82,${(0.06 + norm * 0.22).toFixed(3)})"` : "";
        html += `<td><button class="mx-cell ${on ? "on" : "off"}${active}" data-m="${m.id}" data-c="${c.id}" ${heat}
          title="${on ? `v${cell.version} · ${cell.samples} 样本 · AUC ${auc.toFixed(3)}` : "未学习"}">
          ${on ? `<b>AUC ${auc.toFixed(2)}</b><span>v${cell.version} · ${cell.samples}样本</span><i class="mx-mini"><em style="width:${Math.round(25 + norm * 75)}%"></em></i>` : "<span>未学</span>"}
        </button></td>`;
      });
      html += "</tr>";
    });
    html += "</tbody></table>";
    G.$("#rl-grid").innerHTML = html;
    G.$$("#rl-grid .mx-cell").forEach((el) => el.addEventListener("click", () => {
      G.setCtx({ model: el.dataset.m, channel: el.dataset.c });
    }));
  }

  async function loadCell() {
    const card = G.$("#rl-card");
    const polBox = G.$("#rl-policy");
    card.innerHTML = '<div class="loading">加载规则卡…</div>';
    try {
      const d = await G.api(`/api/rules/cell?model_id=${G.state.model}&channel_id=${G.state.channel}`);
      st.detail = d;
      const rs = d.rule_set;
      G.$("#rl-card-title").textContent = `${d.model.name} × ${d.channel.name} 规则卡`;
      G.$("#rl-card-meta").textContent =
        `v${rs.version} · AUC ${Number(rs.auc).toFixed(3)} · ${rs.samples} 样本 · ` +
        (rs.question_set_id ? "来自测试问题集" : "未接问题集");
      const max = Math.max(...rs.cards.map((c) => Math.abs(c.weight)), 0.001);
      card.innerHTML = `
        <div class="rule-note">${G.esc(rs.notes)}</div>
        <div class="mx-bars">${rs.cards.map((c) => `
          <div class="bar-row">
            <div class="bar-label">${G.esc(c.label)}${c.stable === false ? '<span class="mx-tag warn" title="与单变量相关方向相反，受共线性影响">不稳</span>' : ""}</div>
            <div class="bar-track"><div class="bar-fill ${c.weight >= 0 ? "pos" : "neg"}"
              style="width:${Math.max(4, (Math.abs(c.weight) / max) * 100)}%"></div></div>
            <div class="bar-val">${c.weight > 0 ? "+" : ""}${c.weight.toFixed(2)}</div>
            <div class="bar-note">${G.esc(c.advice)}</div>
          </div>`).join("")}</div>
        ${rs.queries_used && rs.queries_used.length ? `
          <div class="sub-title">本次学习用到的问法（${rs.queries_used.length} 条）</div>
          <div class="chips">${rs.queries_used.slice(0, 10).map((q) => `<span class="chip">${G.esc(q)}</span>`).join("")}</div>
          <div class="note">其余 ${Math.max(0, rs.queries_used.length - 10)} 条略</div>` : ""}`;

      polBox.innerHTML = `
        <div class="kv"><span>字数</span><b>${d.channel.min_words} – ${d.channel.max_words}</b><em>硬约束</em></div>
        <div class="kv"><span>外链</span><b>≤ ${d.channel.max_links} 个</b><em>${d.channel.max_links === 0 ? "站外链接不可点" : ""}</em></div>
        <div class="kv"><span>风格</span><b>${G.esc(d.channel.style)}</b></div>
        <div class="chips" style="margin-top:8px">${d.channel.constraints.map((c) => `<span class="chip">${G.esc(c)}</span>`).join("")}</div>
        <div class="sub-title">信息源网站实测特征（本地实采 HTML）</div>
        <div id="rl-src-prof" class="note">加载实采特征…</div>
        <div class="sub-title">模型侧</div>
        <div class="note">${G.esc(d.model.note)}<br>地区：${d.model.vendor} · 检索能力：${d.model.retrieval ? "有" : "无"}（无检索则无法产生引用）</div>`;
      G.$("#rl-policy-meta").textContent = d.channel.name;
      G.api(`/api/sources/profiles/${G.state.channel}`).then((sp) => {
        const box = G.$("#rl-src-prof");
        if (!box) return;
        const p = sp && sp.profile;
        if (!p) { box.textContent = "该渠道暂无本地实采 HTML 样本（改写将使用静态渠道规范）"; return; }
        const s = p.stats;
        box.innerHTML = `
          <div class="rule-note good" style="margin-bottom:6px">
            已采 <b>${p.sample_count}</b> 篇真实网页 · 均长 ${Math.round(s.avg_chars)} 字 · 均段长 ${Math.round(s.avg_para_len)} 字 ·
            小标题 ${s.avg_headings} 个 · 列表 ${s.avg_list_items} 项 · 数据密度 ${s.numbers_per_1k}/千字 · 实测 GQS ${s.avg_gqs}
          </div>
          ${p.sample_headings && p.sample_headings.length
            ? `<div class="chips">${p.sample_headings.slice(0, 4).map((h) => `<span class="chip">${G.esc(h.slice(0, 22))}</span>`).join("")}</div>`
            : ""}`;
      }).catch(() => {});
    } catch (e) {
      G.$("#rl-card-title").textContent = "该格规则卡";
      G.$("#rl-card-meta").textContent = "";
      card.innerHTML = `<div class="empty">${G.esc(e.message)}</div>
        <button class="btn" id="rl-card-learn">学习该格</button>`;
      const b = G.$("#rl-card-learn");
      if (b) b.onclick = () => learnOne();
      G.api(`/api/mx/policy/${G.state.channel}`).then((p) => {
        polBox.innerHTML = `<div class="kv"><span>字数</span><b>${p.min_words} – ${p.max_words}</b></div>
          <div class="kv"><span>外链</span><b>≤ ${p.max_links}</b></div>
          <div class="note">${G.esc(p.style)}</div>`;
        G.$("#rl-policy-meta").textContent = p.name;
      }).catch(() => { polBox.innerHTML = ""; });
    }
  }

  async function learnOne() {
    if (st.busy) return;
    st.busy = true;
    const btn = G.$("#rl-learn-cell");
    btn.disabled = true; btn.textContent = "学习中…";
    try {
      const d = await G.post("/api/rules/learn", {
        model_id: G.state.model, channel_id: G.state.channel, target_id: G.state.target,
      });
      G.toast(`已学：AUC ${Number(d.auc).toFixed(3)} · ${d.samples} 样本 · 用了 ${d.used_questions} 条问法`);
      await loadMatrix();
      await loadCell();
      G.loadProgress();
    } catch (e) {
      G.toast(`学习失败：${e.message}`, true);
    } finally {
      st.busy = false;
      btn.disabled = false; btn.textContent = "重学该格";
    }
  }

  async function learnAll() {
    if (st.busy) return;
    st.busy = true;
    const btn = G.$("#rl-learn-all");
    const total = (G.catalog ? G.catalog.models.length : 6) * (G.catalog ? G.catalog.channels.length : 11);
    btn.disabled = true; btn.textContent = `学习中（${total} 格）…`;
    try {
      const d = await G.post("/api/rules/learn_all", { target_id: G.state.target });
      const errs = d.errors.length ? `，${d.errors.length} 格失败` : "";
      G.toast(`已学 ${d.learned} 格 · 用了 ${d.used_questions} 条问法${errs}`);
      await loadMatrix();
      await loadCell();
      G.loadProgress();
    } catch (e) {
      G.toast(`学习失败：${e.message}`, true);
    } finally {
      st.busy = false;
      btn.disabled = false; btn.textContent = "按当前目标物学习全部格子";
    }
  }

  function refresh() {
    if (!G._mounted.rules) return;
    loadMatrix();
    loadCell();
    loadChecks();
    // 维度标签缓存：把问题集里的维度中文标签带过来
    G.api(`/api/questions/dimension_sets?target_id=${G.state.target}`).then((d) => {
      st._dimLabels = {};
      d.sets.forEach((s) => { st._dimLabels[s.dimension] = s.label; });
      loadPref();
    }).catch(() => loadPref());
  }

  return { mount, refresh };
})();

G.register("rules", RulesView);
