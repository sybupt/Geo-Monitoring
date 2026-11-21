/* 6 效果看板：基线 → 复测，同一批问法，z 检验。看板按模型查看，页内切换。 */
const BoardView = (() => {
  const st = { data: null };

  function mount() {
    G.$("#bd-models").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-m]");
      if (b && b.dataset.m !== G.state.model) G.setCtx({ model: b.dataset.m });
    });
  }

  function renderModels() {
    const box = G.$("#bd-models");
    box.innerHTML = (G.catalog ? G.catalog.models : []).map((m) =>
      `<button class="chip-btn${m.id === G.state.model ? " on" : ""}" data-m="${m.id}">
        ${G.esc(m.name)}</button>`).join("") +
      `<span class="note" style="margin-left:6px;align-self:center">看板按模型查看，点击切换</span>`;
  }

  async function load() {
    const cards = G.$("#bd-cards");
    renderModels();
    try {
      const d = await G.api(`/api/board?target_id=${G.state.target}&model_id=${G.state.model}`);
      st.data = d;
      const o = d.overall;
      const rcTotal = o.recheck_total || 0;
      cards.innerHTML = [
        ["基线提及率", o.baseline_avg === null ? "—" : G.pct(o.baseline_avg), "全部渠道均值"],
        ["最新复测提及率", o.after_avg === null ? "—" : G.pct(o.after_avg),
          rcTotal ? `${rcTotal} 次复测 · 覆盖 ${o.recheck_channels} 个渠道` +
            (o.recheck_voided ? ` · ${o.recheck_voided} 次失效` : "") : "尚未复测"],
        ["平均提升", o.avg_delta === null ? "—" : G.signed(o.avg_delta * 100, 1, "pt"),
          `${o.channels_with_data} 个渠道有前后数据`],
        ["显著提升渠道", String(o.significant), "|z| ≥ 1.96"],
        ["已投喂渠道", `${o.fed}`, `共 ${d.rows.length} 个渠道`],
      ].map(([l, v, s]) => `<div class="card"><div class="label">${l}</div>
        <div class="value">${v}</div><div class="note">${G.esc(s)}</div></div>`).join("");

      // 复测列渲染：第 n 次复测的命中率 + 真实间隔 + 相对基线的变化
      const rcCell = (list, n) => {
        const rc = list && list.find((x) => x.no === n);
        if (!rc) return "—";
        const bits = [];
        if (rc.days !== null && rc.days !== undefined) bits.push(`投喂后 ${rc.days} 天`);
        if (rc.delta !== null && rc.delta !== undefined)
          bits.push(`<span class="${rc.delta > 0 ? "up" : rc.delta < 0 ? "down" : ""}">${G.signed(rc.delta * 100, 1, "pt")}</span>`);
        return `${G.pct(rc.hit_rate)}<div class="note">${bits.join(" · ") || "—"}</div>`;
      };

      G.$("#bd-table").innerHTML = `
        <table>
          <thead><tr>
            <th>渠道</th><th>规则</th><th class="num">基线</th>
            <th class="num">复测1</th><th class="num">复测2</th>
            <th class="num">最新变化</th><th class="num">z</th><th>状态</th><th>趋势</th>
          </tr></thead>
          <tbody>${d.rows.map((r) => `<tr>
            <td><b>${G.esc(r.channel_name)}</b><div class="note">${r.locale === "en" ? "英文" : "中文"}${r.voided_rechecks ? ` · 另有 ${r.voided_rechecks} 次复测因登记修改失效` : ""}</div></td>
            <td>${r.learned ? `<span class="tag geo">v${r.rule_version}</span>` : '<span class="tag">未学</span>'}</td>
            <td class="num">${r.baseline ? G.pct(r.baseline.hit_rate) : "—"}</td>
            <td class="num">${rcCell(r.rechecks, 1)}</td>
            <td class="num">${rcCell(r.rechecks, 2)}</td>
            <td class="num ${r.delta > 0 ? "up" : r.delta < 0 ? "down" : ""}">
              ${r.delta === null ? "—" : G.signed(r.delta * 100, 1, "pt")}</td>
            <td class="num">${r.z === null ? "—" : (r.z >= 1.96 ? `<b class="up">${r.z}</b>` : r.z)}</td>
            <td>${G.statusPill(r.status, r.status_label)}</td>
            <td>${G.spark(r.trend)}</td>
          </tr>`).join("")}</tbody>
        </table>
        <div class="note" style="margin-top:10px">${G.esc(d.note)}</div>`;

      const feedRc = (f, n) => {
        const rc = f.rechecks && f.rechecks.find((x) => x.no === n);
        if (!rc) return "—";
        const bits = [];
        if (rc.days !== null && rc.days !== undefined) bits.push(`投喂后 ${rc.days} 天`);
        if (rc.delta !== null && rc.delta !== undefined)
          bits.push(`<span class="${rc.delta > 0 ? "up" : rc.delta < 0 ? "down" : ""}">${G.signed(rc.delta * 100, 1, "pt")}</span>`);
        return `${G.pct(rc.hit_rate)}<div class="note">${bits.join(" · ") || "—"}</div>`;
      };
      G.$("#bd-feeds").innerHTML = d.feeds.length
        ? `<table><thead><tr><th>渠道</th><th>目标模型</th><th>链接</th><th class="num">时长</th><th>状态</th>
           <th class="num">复测1</th><th class="num">复测2</th><th class="num">投喂时间</th></tr></thead>
           <tbody>${d.feeds.map((f) => `<tr>
             <td>${G.esc((G.channelMap[f.channel_id] || {}).name || f.channel_id)}</td>
             <td>${G.esc((G.modelMap[f.model_id] || {}).name || f.model_id)}</td>
             <td>${f.url ? `<a href="${G.esc(f.url)}" target="_blank" rel="noreferrer">${G.esc(f.url)}</a>` : '<span class="muted">未回填</span>'}</td>
             <td class="num">${f.duration_days ? f.duration_days + " 天" : "—"}</td>
             <td>${G.esc(f.status)}</td>
             <td class="num">${feedRc(f, 1)}</td>
             <td class="num">${feedRc(f, 2)}</td>
             <td class="num muted">${G.esc(f.created_at)}</td>
           </tr>`).join("")}</tbody></table>
           <div class="note" style="margin-top:10px">同一渠道（每模型）只显示最新一次登记——「登记修改」直接覆盖旧记录；修改前的复测全部失效，不计入统计。</div>`
        : '<div class="empty">还没有投喂记录</div>';
      G.$("#bd-feed-meta").textContent = `${d.feeds.length} 条`;
    } catch (e) {
      cards.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  function refresh() {
    load();
  }

  return { mount, refresh };
})();

G.register("board", BoardView);
