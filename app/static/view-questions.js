/* 2 测试问题集：按目标物行业生成多维度问题集（不绑模型/渠道），
   再测全部模型的提及率基线，读出每个模型的维度偏好。 */
const QuestionsView = (() => {
  const st = { sets: [], baseline: null, busy: false };

  function mount() {
    G.$("#qz-gen-dims").addEventListener("click", genDims);
    G.$("#qz-baseline").addEventListener("click", runBaseline);
  }

  async function genDims() {
    const btn = G.$("#qz-gen-dims");
    btn.disabled = true; btn.textContent = "生成中…";
    try {
      const d = await G.post("/api/questions/dimension_sets", { target_id: G.state.target });
      G.toast(`已生成 ${d.total_sets} 个维度、共 ${d.total_questions} 条问法（${d.industry}）`);
      await loadSets();
      G.loadProgress();
    } catch (e) {
      G.toast(`生成失败：${e.message}`, true);
    } finally {
      btn.disabled = false; btn.textContent = "按行业生成维度问题集";
    }
  }

  async function loadSets() {
    const box = G.$("#qz-list");
    try {
      const d = await G.api(`/api/questions/dimension_sets?target_id=${G.state.target}`);
      st.sets = d.sets;
      const total = d.sets.reduce((a, s) => a + s.total, 0);
      G.$("#qz-meta").textContent =
        `${d.industry}行业 · ${d.sets.length} 个维度 · ${total} 条问法`;
      if (!d.sets.length) {
        box.innerHTML = `<div class="empty">还没有维度问题集，点右上「按行业生成维度问题集」</div>`;
        return;
      }
      box.innerHTML = `<div class="tgrid">${d.sets.map((s) => `
        <div class="tcard" style="cursor:default">
          <div class="tc-head">
            <div>
              <div class="tc-title">${G.esc(s.label)}问题集</div>
              <div class="tc-sub">${s.total} 条 · ${G.esc(s.created_at || "")}</div>
            </div>
            <span class="tag">${G.esc(s.dimension)}</span>
          </div>
          <div class="tc-intro" style="-webkit-line-clamp:3">${s.questions.map((q) =>
            G.esc(q)).join("<br>")}</div>
        </div>`).join("")}</div>`;
    } catch (e) {
      box.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  function dimLabel(dim) {
    const s = st.sets.find((x) => x.dimension === dim);
    return s ? s.label : dim;
  }

  async function loadBaseline() {
    const box = G.$("#qz-base");
    try {
      const d = await G.api(`/api/questions/baseline_matrix?target_id=${G.state.target}`);
      if (!d.models.length) { box.innerHTML = '<div class="empty">还没测过基线</div>'; return; }
      st.baseline = d;
      const models = G.catalog.models.filter((m) => d.models.includes(m.id));
      const head = `<tr><th>维度 \\ 模型</th>${models.map((m) =>
        `<th>${G.esc(m.name)}</th>`).join("")}<th class="num">维度均值</th></tr>`;
      const rows = d.dimensions.map((dim) => {
        let sum = 0;
        const cells = models.map((m) => {
          const v = d.matrix[`${m.id}::${dim}`];
          if (v) sum += v.hit_rate;
          return `<td class="num">${v ? G.pct(v.hit_rate) : "—"}</td>`;
        }).join("");
        const n = models.length || 1;
        return `<tr><td><b>${G.esc(dimLabel(dim))}</b></td>${cells}
          <td class="num muted">${G.pct(sum / n)}</td></tr>`;
      }).join("");
      const overall = d.models.map((mid) => {
        const vals = d.dimensions.map((dim) => (d.matrix[`${mid}::${dim}`] || {}).hit_rate).filter((v) => v !== undefined);
        const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
        return `<td class="num"><b>${avg === null ? "—" : G.pct(avg)}</b></td>`;
      }).join("");
      box.innerHTML = `
        <table><thead>${head}</thead><tbody>${rows}
          <tr><td><b>模型均值</b></td>${overall}<td class="num muted">—</td></tr>
        </tbody></table>
        <div class="note" style="margin-top:10px">
          基线 = 该维度问法在对应模型下，目标物原稿被引用的比例（全渠道通用）。
          品牌问法天然占优；泛问法低说明目标物要靠投喂内容去挤占位。
        </div>`;
    } catch (e) {
      box.innerHTML = `<div class="empty">${G.esc(e.message)}</div>`;
    }
  }

  async function runBaseline() {
    const btn = G.$("#qz-baseline");
    btn.disabled = true; btn.textContent = "测量中（全模型）…";
    try {
      const d = await G.post("/api/questions/baseline_all", { target_id: G.state.target });
      G.toast(`基线完成：${d.models.length} 个模型 × ${d.dimensions.length} 个维度，总体 ${G.pct(d.hit_rate)}`);
      await loadBaseline();
      G.loadProgress();
    } catch (e) {
      G.toast(`基线失败：${e.message}`, true);
    } finally {
      btn.disabled = false; btn.textContent = "测全部模型基线";
    }
  }

  function refresh() {
    if (!G._mounted.questions) return;
    loadSets();
    loadBaseline();
  }

  return { mount, refresh, loadSets, loadBaseline };
})();

G.register("questions", QuestionsView);
