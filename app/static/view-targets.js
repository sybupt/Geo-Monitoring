/* 1 推荐流：要被投喂、要被引用的目标物。
   列表给"这东西是什么"，详情给"它在某个模型×渠道下的现状与画像"。 */
const TargetsView = (() => {
  const st = { items: [], total: 0, detail: null, busy: false };

  function mount() {
    const q = G.$("#rec-q");
    q.addEventListener("input", () => {
      clearTimeout(window.__rq);
      window.__rq = setTimeout(() => { G.state.q = q.value.trim(); loadList(); }, 250);
    });
    G.$("#rec-sort").addEventListener("change", (e) => {
      G.state.sort = e.target.value;
      loadList();
    });
  }

  async function loadCats() {
    const all = ["", ...new Set(G.targets.map((t) => t.category))];
    G.$("#rec-cats").innerHTML = all.map((c) => `
      <button class="chip-btn ${G.state.cat === c ? "on" : ""}" data-cat="${G.esc(c)}">
        ${c ? G.esc(c) : "全部"}</button>`).join("");
    G.$$("#rec-cats .chip-btn").forEach((b) => b.addEventListener("click", () => {
      G.state.cat = b.dataset.cat;
      loadCats();
      loadList();
    }));
  }

  async function loadList() {
    const grid = G.$("#rec-grid");
    grid.innerHTML = '<div class="loading">加载目标物…</div>';
    try {
      const p = new URLSearchParams({
        q: G.state.q, sort: G.state.sort, limit: "30", category: G.state.cat,
      });
      const data = await G.api(`/api/targets?${p.toString()}`);
      st.items = data.items;
      st.total = data.total;
      G.$("#rec-meta").textContent = `${data.total} 个目标物 · 按${labelOf(G.state.sort)}排序`;
      if (!data.items.length) {
        grid.innerHTML = '<div class="empty">没有匹配的目标物</div>';
        return;
      }
      grid.innerHTML = data.items.map(card).join("");
      G.$$("#rec-grid .tcard").forEach((el) => el.addEventListener("click", () => {
        openDetail(el.dataset.id);
      }));
    } catch (e) {
      grid.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  function labelOf(sort) {
    return { gqs_asc: "内容分最低", gqs: "内容分最高", hot: "热度", price: "价格", new: "上架时间" }[sort] || sort;
  }

  function card(t) {
    const active = t.item_id === G.state.target;
    const promote = t.promote_type === "product"
      ? `<span class="tag geo">推广产品</span>` : `<span class="tag">推广品牌</span>`;
    return `
      <div class="tcard ${active ? "active" : ""}" data-id="${t.item_id}">
        <div class="tc-head">
          <div>
            <div class="tc-title">${G.esc(t.member_name || t.title)}</div>
            <div class="tc-sub">${G.esc(t.category)} · 目标物：${G.esc(t.title)}</div>
          </div>
          ${G.scoreBadge(t.gqs)}
        </div>
        <div class="tc-promote">${promote} <b>${G.esc(t.promote_name || t.brand)}</b></div>
        <div class="tc-intro">${G.esc(t.intro)}…</div>
        <div class="tc-foot">
          <span class="tag geo">${G.esc(t.plan_label || "试用")} · ${t.plan_channels || 0} 渠道</span>
          <span class="tag">${t.text_len} 字</span>
          ${t.baseline_cells ? `<span class="tag">基线 ${t.baseline_cells} 格</span>` : '<span class="tag">未测基线</span>'}
          ${t.feed_cells ? `<span class="tag">已投喂 ${t.feed_cells} 渠道</span>` : ""}
          ${t.is_cold ? '<span class="tag warn">新品</span>' : ""}
        </div>
      </div>`;
  }

  async function openDetail(itemId) {
    st.detail = itemId;
    G.setCtx({ target: itemId });
    await renderDetail();
    const el = G.$("#rec-detail");
    if (el) el.scrollIntoView({ block: "start" });
  }

  async function renderDetail() {
    const box = G.$("#rec-detail");
    if (!st.detail) { box.innerHTML = ""; return; }
    box.innerHTML = '<div class="panel detail"><div class="loading">加载画像…</div></div>';
    try {
      const [d, cell] = await Promise.all([
        G.api(`/api/targets/${st.detail}?model_id=${G.state.model}`),
        G.api(`/api/targets/${st.detail}/cell?model_id=${G.state.model}&channel_id=${G.state.channel}`),
      ]);
      box.innerHTML = detailHtml(d, cell);
      G.$("#dt-open-q").addEventListener("click", () => G.open("questions"));
      G.$("#dt-text-toggle").addEventListener("click", () => {
        const pre = G.$("#dt-text");
        pre.style.display = pre.style.display === "none" ? "block" : "none";
      });
      const planSel = G.$("#dt-plan-sel");
      if (planSel) planSel.addEventListener("change", () => changePlan(planSel.value));
    } catch (e) {
      box.innerHTML = `<div class="panel detail"><div class="empty">${G.esc(e.message)}</div></div>`;
    }
  }

  function personaRows(list) {
    const max = Math.max(...list.map((p) => p.share), 0.01);
    return list.map((p) => `
      <div class="mini-row">
        <span>${G.esc(p.name)}</span>
        <div class="track"><div class="fill" style="width:${(p.share / max) * 100}%"></div></div>
        <b>${G.pct(p.share)}</b>
      </div>`).join("");
  }

  async function changePlan(plan) {
    try {
      await G.post(`/api/membership/${st.detail}`, { plan });
      G.toast("会员套餐已更新，第 4 步可投放渠道随之变化");
      await renderDetail();
    } catch (e) {
      G.toast(`更新失败：${e.message}`, true);
    }
  }

  function detailHtml(d, cell) {
    const c = d.card;
    const p = cell.profile;
    const v = cell.view;
    const stt = cell.status;
    const geo = d.geo;
    const comp = v.competitors.map((x) => `
      <div class="comp-row">
        <span class="comp-title">${G.esc(x.title)}</span>
        <div class="track"><div class="fill alt" style="width:${x.score * 100}%"></div></div>
        <b>${G.num(x.score, 3)}</b>
      </div>`).join("");

    return `
    <div class="panel detail">
      <div class="panel-head">
        <h2>${G.esc(c.member_name || c.title)}</h2>
        <span class="hint">${G.esc(c.category)} · 推广${c.promote_type === "product" ? "产品" : "品牌"}：${G.esc(c.promote_name)} · 目标物 ${G.esc(c.title)} · ${c.text_len} 字</span>
        <div style="flex:1"></div>
        <button class="btn ghost" id="dt-text-toggle">原稿</button>
        <button class="btn" id="dt-open-q">用这个画像生成测试问题 →</button>
      </div>
      <div class="panel-body">
        <div class="note" style="margin-bottom:12px">
          会员：<b>${G.esc((d.membership || {}).plan_label || "试用（免费）")}</b>
          （${G.esc((d.membership || {}).price || "免费")}）·
          可投放 ${(d.membership || {}).channels ? d.membership.channels.length : 0} 个渠道：
          ${((d.membership || {}).channels || []).map((ch) => G.esc(ch.name)).join("、")}
          <select id="dt-plan-sel" style="margin-left:8px;padding:4px 8px;font-size:12px">
            ${(G.catalog.plans || []).map((p) => `
              <option value="${p.id}" ${(d.membership || {}).plan === p.id ? "selected" : ""}>
                切换为 ${G.esc(p.label)}（${G.esc(p.price)}）</option>`).join("")}
          </select>
        </div>
        <div class="dt-grid">
          <div class="dt-col">
            <div class="sec-title">目标物概况</div>
            <div class="kv"><span>内容质量分 GQS</span><b>${geo.gqs} · ${geo.grade} 级 ${G.esc(geo.grade_label)}</b></div>
            <div class="kv"><span>品牌 / 类目</span><b>${G.esc(c.brand)} / ${G.esc(c.category)}</b></div>
            <div class="kv"><span>价格位置</span><b>${G.esc(p.audience.price_position.text)}</b></div>
            <div class="kv"><span>同类竞争</span><b>${p.audience.competition.peer_items} 个型号</b></div>
            <pre class="item-text" id="dt-text" style="display:none;margin-top:10px">${G.esc(d.text)}</pre>
            <div class="sec-title" style="margin-top:14px">最需要补的缺口</div>
            <div class="chips">${p.gaps.map((g) =>
              `<span class="chip bad" title="${G.esc(g.hint)}">${G.esc(g.label)} ${g.score}</span>`).join("") || '<span class="note">已达标</span>'}</div>
          </div>

          <div class="dt-col">
            <div class="sec-title">目标画像 · ${G.esc(G.model().name)} × ${G.esc(G.channel().name)}</div>
            <div class="note" style="margin-bottom:6px">${G.esc(p.audience.summary)}</div>
            <div class="mini-list">${personaRows(p.audience.personas)}</div>
            <div class="sub-title">关注点</div>
            <div class="chips">${p.needs.aspects.map((a) => `<span class="chip">${G.esc(a)}</span>`).join("")}</div>
            <div class="sub-title">场景</div>
            <div class="chips">${p.needs.scenes.map((a) => `<span class="chip">${G.esc(a)}</span>`).join("")}</div>
            <div class="sub-title">必提信息</div>
            <ul class="plain">${p.must_mention.map((m) => `<li>${G.esc(m)}</li>`).join("")}</ul>
            <div class="sub-title">该模型偏好（${p.rule_version ? `v${p.rule_version} 已学` : "先验"}）</div>
            <div class="chips">${p.model.prefs.map((x) =>
              `<span class="chip ${x.source === "learned" ? "geo" : ""}">${G.esc(x.label)} ${x.source === "learned" ? (x.weight > 0 ? "+" : "") + x.weight : ""}</span>`).join("")}</div>
            <div class="rule-note" style="margin-top:10px">切入角度：${G.esc(p.angle)}</div>
          </div>

          <div class="dt-col">
            <div class="sec-title">当前时间点的渠道现状</div>
            <div class="kv"><span>查询问法</span><b>${G.esc(v.query)}</b></div>
            <div class="kv"><span>目标物当前位次</span><b>${v.own_rank ? "#" + v.own_rank : "未进前 8"}</b></div>
            <div class="kv"><span>检索分</span><b>${G.num(v.own_score, 3)}</b></div>
            <div class="kv"><span>基线提及率</span><b>${stt.baseline ? G.pct(stt.baseline.hit_rate) : "未测"}</b></div>
            <div class="kv"><span>复测提及率</span><b>${stt.after ? G.pct(stt.after.hit_rate) : "—"}</b></div>
            <div class="kv"><span>渠道状态</span><b>${G.statusPill(stt.status, stt.status_label)}</b></div>
            <div class="sub-title">排在前面的内容（要挤掉的对象）</div>
            ${comp || '<div class="note">该问法下暂无其它内容</div>'}
            <div class="note" style="margin-top:10px">
              模型 ${G.esc(G.model().name)}：${G.esc(p.model.note)}<br>
              渠道 ${G.esc(G.channel().name)}：${G.esc(p.channel.style)}
            </div>
          </div>
        </div>

        <div class="dt-rules">
          ${geo.rules.map((r) => `
            <div class="rule-chip ${r.score < 0.5 ? "bad" : r.score < 0.75 ? "warn" : "ok"}"
                 title="${G.esc(r.hint)}">
              <div class="rc-top"><span>${G.esc(r.label)}</span><b>${r.score.toFixed(2)}</b></div>
              <div class="rc-bar"><i style="width:${Math.max(4, Math.min(100, r.score * 100))}%"></i></div>
            </div>`).join("")}
        </div>
      </div>
    </div>`;
  }

  function refresh() {
    if (!G._mounted.rec) return;
    loadCats();
    loadList();
    if (st.detail) renderDetail();
  }

  return { mount, refresh, openDetail };
})();

G.register("rec", TargetsView);
