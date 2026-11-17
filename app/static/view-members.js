/* 7 会员管理：会员名（企业主体）、推广对象（品牌/产品）、套餐级别（缴费决定）、到期与续费。
   套餐级别直接决定第 4 步能投放哪些渠道，所以这里改动会立刻影响改写与投喂。 */
const MembersView = (() => {
  const st = { data: null, busy: false };

  function mount() {
    G.$("#mb-table").addEventListener("click", (ev) => {
      const renew = ev.target.closest("button[data-renew]");
      if (renew) { renewPlan(renew.dataset.renew); return; }
      const view = ev.target.closest("button[data-view-target]");
      if (view) { openTarget(view.dataset.viewTarget); return; }
      const ren = ev.target.closest("button[data-rename]");
      if (ren) { renameMember(ren.dataset.rename); }
    });
    G.$("#mb-table").addEventListener("change", (ev) => {
      const sel = ev.target.closest("select[data-plan]");
      if (sel) changePlan(sel.dataset.plan, sel.value);
    });
  }

  async function load() {
    const box = G.$("#mb-table");
    try {
      const d = await G.api("/api/memberships");
      st.data = d;
      renderSummary(d);
      renderPlans(d);
      renderTable(d);
    } catch (e) {
      box.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  function renderSummary(d) {
    const s = d.summary;
    G.$("#mb-cards").innerHTML = [
      ["会员企业", String(s.members), "推荐流里的目标物数量"],
      ["付费会员", String(s.paid_members), `高级 ${s.by_plan.premium || 0} · 普通 ${s.by_plan.standard || 0}`],
      ["年度合同额", `¥${(s.arr / 10000).toFixed(1)} 万`, "按套餐年费合计（不含试用）"],
      ["30 天内到期", String(s.expiring_30d), "需要跟进续费"],
    ].map(([l, v, n]) => `<div class="card"><div class="label">${l}</div>
      <div class="value">${v}</div><div class="note">${G.esc(n)}</div></div>`).join("");
  }

  function renderPlans(d) {
    G.$("#mb-plans").innerHTML = d.plans.map((p) => {
      const own = d.items.filter((x) => x.plan === p.id).length;
      return `<div class="plan-card ${p.id}">
        <div class="pc-head"><b>${G.esc(p.label)}</b><span>${G.esc(p.id === "trial" ? "0 元" : p.price)}</span></div>
        <div class="pc-desc">${G.esc(p.desc)}</div>
        <div class="pc-foot"><span>${p.channels.length} 个投放渠道</span><em>${own} 家会员</em></div>
      </div>`;
    }).join("");
  }

  function renderTable(d) {
    const box = G.$("#mb-table");
    if (!d.items.length) {
      box.innerHTML = '<div class="empty">还没有会员企业</div>';
      return;
    }
    box.innerHTML = `
      <table>
        <thead><tr>
          <th>会员名（企业）</th><th>推广对象</th><th class="num">内容分</th>
          <th>会员级别</th><th>到期</th><th style="min-width:230px">操作</th>
        </tr></thead>
        <tbody>${d.items.map((m) => row(m, d.plans)).join("")}</tbody>
      </table>
      <div class="note" style="margin-top:10px">
        会员级别决定可投放渠道数（试用 2 / 普通 6 / 高级 9）；推广对象可以是企业品牌，也可以是某个具体产品。
      </div>`;
  }

  function row(m, plans) {
    const isTrial = m.plan === "trial";
    const left = m.days_left;
    const leftCls = left !== null && left <= 30 ? "style='color:var(--warn)'" : "";
    const leftTxt = left === null ? "—" : (left < 0 ? `已过期 ${-left} 天` : `剩 ${left} 天`);
    const promo = m.promote_type === "product"
      ? `<span class="tag geo">产品</span> ${G.esc(m.promote_name)}`
      : `<span class="tag">品牌</span> ${G.esc(m.promote_name)}`;
    return `<tr>
      <td>
        <b>${G.esc(m.member_name)}</b>
        <div class="note">${G.esc(m.category)} · 目标物：${G.esc(m.title)}</div>
      </td>
      <td>${promo}</td>
      <td class="num">${m.gqs ? m.gqs.toFixed(0) : "—"}</td>
      <td>
        <select data-plan="${m.target_id}" style="padding:4px 8px;font-size:12px">
          ${plans.map((p) => `<option value="${p.id}" ${p.id === m.plan ? "selected" : ""}>
            ${G.esc(p.label)} · ${G.esc(p.price)}</option>`).join("")}
        </select>
      </td>
      <td>
        <div>${m.paid_until || "—"}</div>
        <div class="note" ${leftCls}>${leftTxt}</div>
      </td>
      <td>
        <button class="btn ghost" data-view-target="${m.target_id}">查看</button>
        <button class="btn ghost" data-rename="${m.target_id}">改名</button>
        ${isTrial ? "" : `<button class="btn" data-renew="${m.target_id}">续费 1 年</button>`}
      </td>
    </tr>`;
  }

  async function changePlan(targetId, plan) {
    if (st.busy) return;
    st.busy = true;
    try {
      await G.post(`/api/membership/${targetId}`, { plan });
      G.toast("会员级别已更新：可投放渠道随之变化");
      await load();
      G.loadProgress();
    } catch (e) {
      G.toast(`更新失败：${e.message}`, true);
    } finally {
      st.busy = false;
    }
  }

  async function renewPlan(targetId) {
    if (st.busy) return;
    st.busy = true;
    try {
      const d = await G.post(`/api/membership/${targetId}/renew`, { months: 12 });
      G.toast(`已续费一年，到期日 ${d.paid_until}`);
      await load();
    } catch (e) {
      G.toast(`续费失败：${e.message}`, true);
    } finally {
      st.busy = false;
    }
  }

  async function renameMember(targetId) {
    const cur = (st.data.items.find((x) => x.target_id === targetId) || {}).member_name || "";
    const name = window.prompt("会员名（企业主体）", cur);
    if (!name || name.trim() === cur) return;
    const plan = (st.data.items.find((x) => x.target_id === targetId) || {}).plan || "trial";
    try {
      await G.post(`/api/membership/${targetId}`, { plan, member_name: name.trim() });
      G.toast("会员名已更新");
      await load();
      await G.reloadTargets();
    } catch (e) {
      G.toast(`改名失败：${e.message}`, true);
    }
  }

  function openTarget(targetId) {
    G.setCtx({ target: targetId });
    G.open("rec");
    setTimeout(() => G.views.rec && G.views.rec.openDetail(targetId), 60);
  }

  function refresh() {
    if (!G._mounted.members) return;
    load();
  }

  return { mount, refresh };
})();

G.register("members", MembersView);
