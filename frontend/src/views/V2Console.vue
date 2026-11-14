<template>
  <div class="page">
    <div class="hero">
      <div class="eyebrow">P0 · 可信度量台</div>
      <h1>多引擎提及率监测</h1>
      <p>
        每题多次采样，比例带 95% 区间；记忆口径与检索口径分开展示，不跨口径排名。
        所有数字都保留原始回答，客户复测可对得上。
      </p>
    </div>

    <!-- 引擎覆盖 -->
    <section class="section">
      <h2>
        引擎覆盖
        <span class="muted">
          已接线 {{ catalog.coverage?.implemented || 0 }} / {{ catalog.coverage?.total || 7 }}
        </span>
      </h2>
      <div class="engine-grid">
        <div v-for="e in catalog.engines" :key="e.engine" :class="['engine-card', { off: !e.implemented }]">
          <div class="engine-head">
            <span class="engine-name">{{ e.label }}</span>
            <span :class="['pill', e.configured ? 'ok' : 'gray']">
              {{ e.implemented ? (e.configured ? '可跑' : '缺 Key') : '未接线' }}
            </span>
          </div>
          <div class="engine-meta">{{ e.vendor }} · {{ e.tier }}</div>
          <div class="engine-meta">引用：{{ citeLabel(e.citation_type) }}</div>
          <div v-if="e.memory_mode" class="engine-warn">记忆口径（不联网）</div>
          <div v-else-if="!e.implemented" class="engine-hint">{{ e.wire_hint }}</div>
        </div>
      </div>
    </section>

    <!-- 新跑批 -->
    <section class="section">
      <h2>发起一次基线监测</h2>
      <div class="form-grid">
        <label>
          企业
          <select v-model.number="form.business_id">
            <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
          </select>
        </label>
        <label>
          采样次数 N<span class="tip">≥5 才能看出波动</span>
          <input v-model.number="form.n_samples" type="number" min="1" max="20" />
        </label>
        <label>
          阶段
          <select v-model="form.stage">
            <option value="baseline">基线（投喂前）</option>
            <option value="after">复测（投喂后）</option>
            <option value="adhoc">临时</option>
          </select>
        </label>
        <label>
          判定模式
          <select v-model="form.judge_mode">
            <option value="rule_only">规则判定（省钱，先跑通）</option>
            <option value="per_sample">LLM 判定（准，有成本）</option>
          </select>
        </label>
        <label>
          城市
          <input v-model="form.city" placeholder="如：清镇" />
        </label>
        <label>
          品类
          <input v-model="form.categoriesText" placeholder="逗号分隔，如：口腔诊所" />
        </label>
        <label>
          对照组品类<span class="tip">同城不同品类，用于 DID</span>
          <input v-model="form.controlText" placeholder="逗号分隔，如：火锅" />
        </label>
        <label>
          竞品
          <input v-model="form.competitorsText" placeholder="逗号分隔" />
        </label>
      </div>

      <div class="engine-picker">
        <span class="picker-label">参与引擎</span>
        <label v-for="e in runnableEngines" :key="e.engine" class="chk">
          <input type="checkbox" :value="e.engine" v-model="form.engines" />
          {{ e.label }}
          <em v-if="e.memory_mode">记忆</em>
        </label>
        <span v-if="!runnableEngines.length" class="muted">没有可跑的引擎，先在 .env 配 Key</span>
      </div>

      <div class="actions">
        <button class="btn ghost" @click="doPreview" :disabled="busy.preview">
          {{ busy.preview ? '生成中…' : '预览问题集' }}
        </button>
        <button class="btn" @click="doCreate" :disabled="busy.create || !form.engines.length">
          {{ busy.create ? '提交中…' : '开始跑批' }}
        </button>
        <span v-if="preview" class="muted">
          共 {{ preview.total }} 题（处理组 {{ preview.treated }} / 对照组 {{ preview.control }}）
          <span v-for="(v, k) in preview.by_intent" :key="k"> · {{ k }} {{ v }}</span>
        </span>
        <span v-if="errMsg" class="err">{{ errMsg }}</span>
      </div>
    </section>

    <!-- 批次列表 -->
    <section class="section">
      <h2>历史批次</h2>
      <table class="table">
        <thead>
          <tr><th>批次</th><th>阶段</th><th>状态</th><th>进度</th><th>N</th><th>引擎</th><th>备注</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.batch_id">
            <td>#{{ b.batch_id }}</td>
            <td>{{ stageLabel(b.stage) }}</td>
            <td><span :class="['pill', statusClass(b.status)]">{{ b.status }}</span></td>
            <td>{{ b.progress }}<span v-if="b.failed" class="err"> · 失败 {{ b.failed }}</span></td>
            <td>{{ b.n_samples }}</td>
            <td class="muted">{{ b.engines.join(', ') }}</td>
            <td class="muted">{{ b.note }}</td>
            <td><button class="btn tiny" @click="openBatch(b.batch_id)">看报告</button></td>
          </tr>
          <tr v-if="!batches.length"><td colspan="8" class="muted">还没有批次</td></tr>
        </tbody>
      </table>
    </section>

    <!-- 报告 -->
    <section v-if="report" class="section">
      <h2>
        报告 #{{ report.batch_id }}
        <span class="muted">{{ report.business?.name }} · {{ stageLabel(report.stage) }} · {{ report.total_samples }} 条有效采样</span>
      </h2>

      <div v-if="report.warnings?.length" class="banner warn">
        <div v-for="(w, i) in report.warnings" :key="i">⚠ {{ w }}</div>
      </div>
      <div class="banner">
        口径：检索型 {{ (report.caliber_notice?.retrieval_engines || []).join('、') || '无' }}
        ｜记忆型 {{ (report.caliber_notice?.memory_engines || []).join('、') || '无' }}
        ｜{{ report.caliber_notice?.warning }}
      </div>

      <div v-for="(e, key) in report.engines" :key="key" class="engine-report">
        <div class="engine-report-head">
          <strong>{{ labelOf(key) }}</strong>
          <span class="pill gray">{{ e.tier }}</span>
          <span v-if="e.memory_mode" class="pill warn">记忆口径</span>
          <span class="muted">n={{ e.n }} · 模型 {{ (e.model_versions || []).join(', ') }}</span>
        </div>

        <div class="metrics">
          <div class="metric-card">
            <div class="metric-label">提及率</div>
            <div class="metric-value">{{ pct(e.mention_rate?.rate) }}</div>
            <div class="metric-ci">95% 区间 {{ ciText(e.mention_rate) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">首位提及率</div>
            <div class="metric-value">{{ pct(e.first_mention_rate?.rate) }}</div>
            <div class="metric-ci">
              区间 {{ ciText(e.first_mention_rate) }} · 名次可判 {{ pct(e.rank_coverage, 0) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">平均名次</div>
            <div class="metric-value">{{ e.avg_rank ?? '—' }}</div>
            <div class="metric-ci">仅统计能判名次的 {{ e.rank_known }} 条</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">引用份额 SoV</div>
            <div class="metric-value">{{ pct(e.share_of_voice) }}</div>
            <div class="metric-ci">本品牌 {{ e.sov_detail?.brand }} / 竞品 {{ e.sov_detail?.competitors }}</div>
          </div>
        </div>

        <div class="sub-grid">
          <div>
            <div class="sub-title">推荐倾向</div>
            <div class="bar-row">
              <span>正面</span>
              <div class="bar"><i class="pos" :style="{ width: pct(e.recommend_rate?.rate) }"></i></div>
              <b>{{ pct(e.recommend_rate?.rate) }}</b>
            </div>
            <div class="bar-row">
              <span>负面</span>
              <div class="bar"><i class="neg" :style="{ width: pct(e.negative_rate?.rate) }"></i></div>
              <b>{{ pct(e.negative_rate?.rate) }}</b>
            </div>
            <div class="sub-title" style="margin-top: 14px">引用语义</div>
            <div class="muted small">
              {{ Object.entries(e.citation_types || {}).map(([k, v]) => `${citeLabel(k)} ${v}`).join(' · ') || '无引用数据' }}
            </div>
          </div>
          <div>
            <div class="sub-title">信源类型分布（下一轮投放的依据）</div>
            <div v-for="[name, cnt] in e.source_types || []" :key="name" class="bar-row">
              <span>{{ name }}</span>
              <div class="bar"><i :style="{ width: barWidth(cnt, e.source_types) }"></i></div>
              <b>{{ cnt }}</b>
            </div>
            <div v-if="!e.source_types?.length" class="muted small">该引擎未返回可解析的信源</div>
          </div>
        </div>
      </div>

      <div class="actions">
        <span class="muted">抽检一致率：</span>
        <span v-if="quality">
          {{ quality.audited }} 条 · {{ pct(quality.agreement_rate, 0) }}
          <span :class="['pill', quality.pass ? 'ok' : 'warn']">{{ quality.pass ? '可出报告' : '未达标' }}</span>
          <span class="muted">{{ quality.note }}</span>
        </span>
        <span v-else class="muted">暂无抽检记录</span>
      </div>
    </section>

    <!-- DID -->
    <section class="section">
      <h2>投喂效果归因（DID）</h2>
      <div class="actions">
        <select v-model.number="didForm.before">
          <option :value="null">基线批次</option>
          <option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">#{{ b.batch_id }} {{ stageLabel(b.stage) }}</option>
        </select>
        <select v-model.number="didForm.after">
          <option :value="null">复测批次</option>
          <option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">#{{ b.batch_id }} {{ stageLabel(b.stage) }}</option>
        </select>
        <button class="btn ghost" @click="doDid" :disabled="!didForm.before || !didForm.after">计算</button>
        <span class="muted">需要两批都含对照组样本，否则只能给提示</span>
      </div>

      <table v-if="did" class="table" style="margin-top: 12px">
        <thead><tr><th>引擎</th><th>处理组变化</th><th>对照组变化</th><th>DID</th><th>95% 区间</th><th>结论</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in did.engines" :key="k">
            <td>{{ labelOf(k) }}</td>
            <template v-if="v.did !== undefined">
              <td>{{ pct(v.treated_delta) }}</td>
              <td>{{ pct(v.control_delta) }}</td>
              <td><b>{{ pct(v.did) }}</b></td>
              <td class="muted">[{{ pct(v.ci95?.[0]) }}, {{ pct(v.ci95?.[1]) }}]</td>
              <td>
                <span :class="['pill', v.significant ? 'ok' : 'warn']">
                  {{ v.significant ? '显著' : '不显著（区间跨 0）' }}
                </span>
              </td>
            </template>
            <td v-else colspan="5" class="muted">{{ v.note }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 问题明细 -->
    <section v-if="report?.questions?.length" class="section">
      <h2>问题明细</h2>
      <table class="table">
        <thead>
          <tr><th>问题</th><th>意图</th><th>组别</th><th>各引擎提及率</th></tr>
        </thead>
        <tbody>
          <tr v-for="q in report.questions" :key="q.question_key">
            <td>{{ q.question }}</td>
            <td class="muted">{{ q.intent }}</td>
            <td><span :class="['pill', q.is_control ? 'gray' : 'ok']">{{ q.is_control ? '对照' : '处理' }}</span></td>
            <td class="muted">
              {{ Object.entries(q.engines).map(([eng, v]) => `${labelOf(eng)} ${pct(v.mention_rate?.rate)}`).join(' ｜ ') }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  createBatch, getBatchReport, getBusinesses, getDid, getEngineCatalog,
  getJudgeQuality, listBatches, previewQuestions,
} from '../api'

const catalog = ref({ engines: [], coverage: {} })
const businesses = ref([])
const batches = ref([])
const preview = ref(null)
const report = ref(null)
const quality = ref(null)
const did = ref(null)
const errMsg = ref('')
const busy = reactive({ preview: false, create: false })
const didForm = reactive({ before: null, after: null })
let timer = null

const form = reactive({
  business_id: null,
  engines: [],
  n_samples: 5,
  stage: 'baseline',
  judge_mode: 'rule_only',
  city: '',
  categoriesText: '',
  controlText: '',
  competitorsText: '',
})

const runnableEngines = computed(() => catalog.value.engines.filter((e) => e.implemented))

const splitList = (s) => s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
const labelOf = (key) => catalog.value.engines.find((e) => e.engine === key)?.label || key
const pct = (v, digits = 1) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`)
const ciText = (block) => (block?.ci95 ? `[${pct(block.ci95[0])}, ${pct(block.ci95[1])}]` : '—')
const citeLabel = (t) =>
  ({ answer_level: '答案级', retrieval_pool: '检索面', caller_supplied: '自供候选', none: '无引用', mock: '模拟' }[t] || t || '—')
const stageLabel = (s) => ({ baseline: '基线', after: '复测', adhoc: '临时' }[s] || s)
const statusClass = (s) => ({ done: 'ok', partial: 'warn', failed: 'bad', running: 'warn' }[s] || 'gray')
const barWidth = (cnt, list) => {
  const max = Math.max(...(list || []).map(([, v]) => v), 1)
  return `${Math.round((cnt / max) * 100)}%`
}

async function loadBase() {
  const [cat, biz] = await Promise.all([getEngineCatalog(), getBusinesses()])
  catalog.value = cat
  businesses.value = biz
  const first = biz[0]
  if (first && !form.business_id) {
    form.business_id = first.id
    form.city = first.region || first.city || ''
    form.categoriesText = first.category || ''
  }
  const prefer = runnableEngines.value.filter((e) => e.configured && !e.memory_mode)
  form.engines = (prefer.length ? prefer : runnableEngines.value).map((e) => e.engine)
  await loadBatches()
}

async function loadBatches() {
  if (!form.business_id) return
  batches.value = await listBatches(form.business_id)
}

function payload() {
  return {
    business_id: form.business_id,
    engines: form.engines,
    n_samples: form.n_samples,
    stage: form.stage,
    judge_mode: form.judge_mode,
    competitors: splitList(form.competitorsText),
    city: form.city,
    categories: splitList(form.categoriesText),
    control_categories: splitList(form.controlText),
  }
}

async function doPreview() {
  errMsg.value = ''
  busy.preview = true
  try {
    preview.value = await previewQuestions(payload())
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || e.message
  } finally {
    busy.preview = false
  }
}

async function doCreate() {
  errMsg.value = ''
  busy.create = true
  try {
    const res = await createBatch({ ...payload(), note: '页面发起' })
    await loadBatches()
    openBatch(res.batch_id)
    poll(res.batch_id)
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || e.message
  } finally {
    busy.create = false
  }
}

async function openBatch(id) {
  report.value = await getBatchReport(id)
  try { quality.value = await getJudgeQuality(id) } catch { quality.value = null }
}

function poll(id) {
  clearInterval(timer)
  timer = setInterval(async () => {
    const r = await getBatchReport(id)
    report.value = r
    if (['done', 'partial', 'failed'].includes(r.status)) {
      clearInterval(timer)
      await loadBatches()
    }
  }, 3000)
}

async function doDid() {
  try {
    did.value = await getDid(didForm.before, didForm.after)
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || e.message
  }
}

onMounted(loadBase)
</script>

<style scoped>
.engine-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.engine-card { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
.engine-card.off { background: #fbfcfb; border-style: dashed; }
.engine-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.engine-name { font-weight: 600; }
.engine-meta { font-size: 12px; color: var(--muted); line-height: 1.7; }
.engine-warn { margin-top: 6px; font-size: 12px; color: #b45309; }
.engine-hint { margin-top: 6px; font-size: 11px; color: var(--muted); line-height: 1.5; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 20px; background: #eef2ef; color: var(--muted); }
.pill.ok { background: var(--mint); color: var(--green); }
.pill.warn { background: #fef3c7; color: #92400e; }
.pill.bad { background: #fee2e2; color: #991b1b; }
.pill.gray { background: #f1f5f3; color: var(--muted); }
.form-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); }
.form-grid input, .form-grid select { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; background: #fff; color: var(--ink); }
.tip { font-size: 11px; color: #9db0a8; }
.engine-picker { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: 16px; font-size: 14px; }
.picker-label { font-size: 13px; color: var(--muted); }
.chk { display: inline-flex; align-items: center; gap: 6px; }
.chk em { font-style: normal; font-size: 11px; color: #b45309; }
.actions { display: flex; align-items: center; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
.btn { padding: 9px 18px; border-radius: 8px; border: 1px solid var(--green); background: var(--green); color: #fff; font-size: 14px; cursor: pointer; }
.btn.ghost { background: #fff; color: var(--green); }
.btn.tiny { padding: 4px 12px; font-size: 12px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.err { color: #b91c1c; font-size: 13px; }
.banner { background: #fff; border: 1px solid var(--line); border-left: 3px solid var(--green); border-radius: 10px; padding: 12px 16px; font-size: 13px; color: var(--muted); line-height: 1.8; }
.banner.warn { border-left-color: #f59e0b; background: #fffbeb; color: #92400e; }
.engine-report { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-top: 16px; }
.engine-report-head { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; font-size: 15px; }
.metric-ci { font-size: 12px; color: var(--muted); margin-top: 4px; }
.sub-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; margin-top: 18px; }
.sub-title { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
.bar-row { display: grid; grid-template-columns: 78px 1fr 56px; align-items: center; gap: 10px; font-size: 12px; margin-bottom: 8px; }
.bar { height: 8px; background: #eef3f0; border-radius: 6px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--green); }
.bar i.pos { background: #10b981; }
.bar i.neg { background: #ef4444; }
.small { font-size: 12px; line-height: 1.7; }
@media (max-width: 1100px) {
  .form-grid { grid-template-columns: repeat(2, 1fr); }
  .sub-grid { grid-template-columns: 1fr; }
}
</style>
