<template>
  <div class="page">
    <div class="page-header">
      <h1>检测报告</h1>
      <p class="muted">查看投喂进度、实时复测效果，并生成前后对比报告。</p>
    </div>

    <div class="toolbar">
      <label>当前企业：</label>
      <select v-model="businessId" @change="loadAll">
        <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
    </div>

    <!-- 投喂状态与实时检测 -->
    <section class="card">
      <h2>投喂状态与实时检测</h2>
      <div class="feed-status">
        <template v-if="publishedAssets.length">
          <span v-for="a in publishedAssets" :key="a.id" class="feed-item">
            <span class="channel-tag">{{ a.channel_name }}</span>
            <span class="muted">{{ timeAgo(a.published_at) }}</span>
          </span>
        </template>
        <span v-else class="muted">尚未投喂</span>
      </div>

      <table class="table" v-if="dedupedBaselineRuns.length">
        <thead>
          <tr><th>问题集</th><th>模型</th><th>投喂前引用</th><th>投喂后引用</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in dedupedBaselineRuns" :key="b.run_id">
            <td>{{ questionSetVersion(b.question_set_id) }}</td>
            <td>{{ b.provider_name }}</td>
            <td>{{ b.mention_rate }}%</td>
            <td>{{ afterState(b).state === 'done' ? afterState(b).rate + '%' : '—' }}</td>
            <td>
              <button v-if="afterState(b).state === 'none'" class="btn primary" :disabled="runningAfter" @click="runAfterDetection(b)">投喂后检测</button>
              <span v-else-if="afterState(b).state === 'running'" class="tag active">复测中…</span>
              <span v-else class="tag published">已复测</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有投喂前基线监测。请先在「问题工厂」启动基线监测。</p>
    </section>

    <!-- 创建报告 -->
    <section class="card" v-if="comparablePairs.length && publishedPlans.length">
      <h2>生成效果报告</h2>
      <div class="form-row"><label>报告名称</label>
        <input v-model="reportTitle" class="input" /></div>
      <div class="form-row"><label>检测批次</label>
        <select v-model="pairIndex" class="input">
          <option v-for="(pair, i) in comparablePairs" :key="i" :value="i">
            {{ pair[0].provider_name }} · 投喂前 #{{ pair[0].run_id }} ({{ displayTime(pair[0].time) }}) → 投喂后 #{{ pair[1].run_id }} ({{ displayTime(pair[1].time) }})
          </option>
        </select></div>
      <div class="form-row"><label>投放计划</label>
        <select v-model="feedingPlanId" class="input">
          <option v-for="p in publishedPlans" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select></div>
      <button class="btn primary" :disabled="creating" @click="createReport">
        {{ creating ? '生成中…' : '生成并保存报告' }}
      </button>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <section class="card" v-else>
      <h2>生成效果报告</h2>
      <p class="muted" v-if="!comparablePairs.length">
        需要先用同一个已审核问题集和同一个模型，分别完成「投喂前基线」和「投喂后复测」，才能生成可比较报告。
      </p>
      <p class="muted" v-else>
        请先在「投喂中心」审核素材、发布至少一个渠道并记录公开链接。
      </p>
    </section>

    <!-- 已有报告 -->
    <section class="card" v-if="reports.length">
      <h2>已保存报告</h2>
      <select v-model="selectedReportId" class="input" @change="loadReport">
        <option v-for="r in reports" :key="r.id" :value="r.id">
          #{{ r.id }} · {{ r.title }} · {{ displayTime(r.created_at) }}
        </option>
      </select>

      <div v-if="selectedReport" class="report-view">
        <div class="metrics">
          <div class="metric-card">
            <div class="metric-label">投喂前提及率</div>
            <div class="metric-value">{{ displayRate(selectedReport.snapshot.baseline.mention_rate) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">投喂后提及率</div>
            <div class="metric-value">{{ displayRate(selectedReport.snapshot.after.mention_rate) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">变化</div>
            <div class="metric-value">{{ formatChange(selectedReport.snapshot.change) }}</div>
          </div>
        </div>

        <a class="btn primary" :href="`/api/reports/${selectedReportId}/html`" download>
          下载 HTML 检测报告
        </a>

        <h3>逐题对比</h3>
        <table class="table">
          <thead>
            <tr><th>用户问题</th><th>投喂前</th><th>投喂后</th><th>变化</th></tr>
          </thead>
          <tbody>
            <tr v-for="(q, i) in selectedReport.snapshot.questions" :key="i">
              <td>{{ q.question }}</td>
              <td>{{ q.baseline_mentioned ? '已提及' : '未提及' }}</td>
              <td>{{ q.after_mentioned ? '已提及' : '未提及' }}</td>
              <td><span :class="['tag', outcomeClass(q)]">{{ outcome(q) }}</span></td>
            </tr>
          </tbody>
        </table>

        <div v-for="(q, i) in selectedReport.snapshot.questions" :key="'a' + i" class="answer-pair">
          <details>
            <summary>查看回答：{{ q.question }}</summary>
            <div class="answer-grid">
              <div>
                <b>投喂前回答</b>
                <p>{{ q.baseline_answer || '（无）' }}</p>
                <div class="citations">
                  <template v-if="q.baseline_citations && q.baseline_citations.length">
                    <a v-for="(c, ci) in q.baseline_citations" :key="ci"
                       :href="c.url" target="_blank" rel="noopener" class="citation-link">{{ c.domain || c.url }}</a>
                  </template>
                  <span v-else class="citation-none">未提供引用来源</span>
                </div>
              </div>
              <div>
                <b>投喂后回答</b>
                <p>{{ q.after_answer || '（无）' }}</p>
                <div class="citations">
                  <template v-if="q.after_citations && q.after_citations.length">
                    <a v-for="(c, ci) in q.after_citations" :key="ci"
                       :href="c.url" target="_blank" rel="noopener" class="citation-link">{{ c.domain || c.url }}</a>
                  </template>
                  <span v-else class="citation-none">未提供引用来源</span>
                </div>
              </div>
            </div>
          </details>
        </div>
      </div>
    </section>

    <p v-if="!reports.length && loaded" class="muted">还没有保存的报告。</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api, { getBusinesses } from '../api'

const businesses = ref([])
const businessId = ref(1)
const runs = ref([])
const feedingPlans = ref([])
const reports = ref([])
const questionSets = ref([])
const error = ref('')
const creating = ref(false)
const loaded = ref(false)
const runningAfter = ref(false)

const reportTitle = ref('')
const pairIndex = ref(0)
const feedingPlanId = ref(null)

const selectedReportId = ref(null)
const selectedReport = ref(null)

const baselineRuns = computed(() => runs.value.filter(r => r.status === 'done' && r.stage === 'baseline'))
const afterRuns = computed(() => runs.value.filter(r => r.status === 'done' && r.stage === 'after'))
const afterRunsAll = computed(() => runs.value.filter(r => r.stage === 'after'))

const publishedAssets = computed(() => {
  const result = []
  for (const p of feedingPlans.value) {
    for (const a of p.assets) {
      if (a.status === 'published') result.push(a)
    }
  }
  return result
})

const dedupedBaselineRuns = computed(() => {
  const seen = new Map()
  for (const r of baselineRuns.value) {
    const key = `${r.provider}-${r.question_set_id}`
    const old = seen.get(key)
    if (!old || r.run_id > old.run_id) seen.set(key, r)
  }
  return [...seen.values()].sort((a, b) => b.run_id - a.run_id)
})

const comparablePairs = computed(() => {
  const pairs = []
  for (const b of baselineRuns.value)
    for (const a of afterRuns.value)
      if (b.provider === a.provider && b.question_set_id === a.question_set_id)
        pairs.push([b, a])
  return pairs
})
const publishedPlans = computed(() =>
  feedingPlans.value.filter(p => p.assets.some(a => a.status === 'published'))
)

function displayTime(v) { return v ? v.replace('T', ' ').slice(0, 16) : '—' }
function displayRate(v) { return v != null ? `${v.toFixed(1)}%` : '—' }
function formatChange(v) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)} 个百分点`
}
function questionSetVersion(id) {
  const qs = questionSets.value.find(q => q.id === id)
  return qs ? `v${qs.version}` : '历史问题'
}
function timeAgo(dateStr) {
  if (!dateStr) return '—'
  const diff = Date.now() - new Date(dateStr).getTime()
  const days = Math.floor(diff / 86400000)
  if (days > 0) return `${days}天前`
  const hours = Math.floor(diff / 3600000)
  return hours > 0 ? `${hours}小时前` : '刚刚'
}
function afterState(b) {
  const a = afterRunsAll.value.find(x => x.provider === b.provider && x.question_set_id === b.question_set_id)
  if (!a) return { state: 'none' }
  if (a.status === 'done') return { state: 'done', rate: a.mention_rate }
  return { state: 'running' }
}
function outcome(q) {
  if (!q.baseline_answer && !q.after_answer) return '—'
  if (q.after_mentioned && !q.baseline_mentioned) return '新增提及'
  if (q.baseline_mentioned && q.after_mentioned) return '持续提及'
  if (q.baseline_mentioned && !q.after_mentioned) return '本次未提及'
  return '仍未提及'
}
function outcomeClass(q) {
  const o = outcome(q)
  return { '新增提及': 'active', '持续提及': 'active', '本次未提及': 'draft', '仍未提及': 'draft' }[o] || ''
}

async function loadAll() {
  try {
    const [runsData, plansData, reportsData, qsetsData] = await Promise.all([
      api.get('/monitor-runs', { params: { business_id: businessId.value, limit: 100 } }),
      api.get('/feeding-plans', { params: { business_id: businessId.value } }),
      api.get('/reports', { params: { business_id: businessId.value } }),
      api.get('/question-sets', { params: { business_id: businessId.value } }),
    ])
    runs.value = runsData.data
    feedingPlans.value = plansData.data
    reports.value = reportsData.data
    questionSets.value = qsetsData.data
    const b = businesses.value.find(x => x.id === businessId.value)
    reportTitle.value = b ? `${b.name} GEO 效果检测报告` : 'GEO 效果检测报告'
    if (publishedPlans.value.length) feedingPlanId.value = publishedPlans.value[0].id
    if (reports.value.length && !selectedReportId.value) {
      selectedReportId.value = reports.value[0].id
      await loadReport()
    }
    loaded.value = true
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function loadReport() {
  if (!selectedReportId.value) return
  try {
    const { data } = await api.get(`/reports/${selectedReportId.value}`)
    selectedReport.value = data
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function runAfterDetection(b) {
  runningAfter.value = true
  error.value = ''
  try {
    await api.post('/monitor-runs', {
      business_id: businessId.value,
      stage: 'after',
      providers: [b.provider],
      question_set_id: b.question_set_id,
    })
    await loadAll()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
  finally { runningAfter.value = false }
}

async function createReport() {
  if (!reportTitle.value.trim()) { error.value = '请填写报告名称'; return }
  if (comparablePairs.value.length === 0 || !feedingPlanId.value) { error.value = '请先完成基线、复测和发布'; return }
  const pair = comparablePairs.value[pairIndex.value]
  creating.value = true
  error.value = ''
  try {
    await api.post('/reports', {
      business_id: businessId.value,
      baseline_run_id: pair[0].run_id,
      after_run_id: pair[1].run_id,
      feeding_plan_id: feedingPlanId.value,
      title: reportTitle.value,
    })
    await loadAll()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    if (businesses.value.length) businessId.value = businesses.value[0].id
    await loadAll()
  } catch (e) {
    error.value = e.message
  }
})
</script>

<style scoped>
.card { background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 22px 24px; margin-bottom: 20px; }
.card h2 { margin: 0 0 16px; font-size: 17px; }
.card h3 { font-size: 15px; margin: 22px 0 12px; }
.form-row { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.form-row label { font-size: 14px; color: #70817b; min-width: 80px; padding-top: 8px; }
.input { padding: 8px 12px; border: 1px solid #e4ebe6; border-radius: 8px; font-size: 14px; background: #fff; flex: 1; }
.btn { padding: 9px 16px; border: 1px solid #e4ebe6; border-radius: 9px; background: #fff; cursor: pointer; font-size: 14px; text-decoration: none; color: #182d2a; display: inline-block; }
.btn.primary { background: #116b55; color: #fff; border-color: #116b55; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.error { color: #b91c1c; font-size: 13px; margin-top: 10px; }
.feed-status { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; font-size: 14px; }
.feed-item { display: flex; align-items: center; gap: 6px; }
.channel-tag { background: #e9f5ef; color: #116b55; padding: 3px 12px; border-radius: 999px; font-size: 13px; }
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 18px 0; }
.metric-card { background: #f8faf8; border: 1px solid #e4ebe6; border-radius: 12px; padding: 16px 18px; }
.metric-label { color: #70817b; font-size: 13px; margin-bottom: 6px; }
.metric-value { font-size: 24px; font-weight: 700; color: #182d2a; }
.table { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 8px; }
.table th, .table td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #e4ebe6; }
.table th { background: #f0f5f1; color: #116b55; font-weight: 600; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.tag.active { background: #e9f5ef; color: #116b55; }
.tag.draft { background: #fff7e6; color: #b45309; }
.tag.published { background: #eef2ff; color: #4f46e5; }
.answer-pair details { margin: 10px 0; border: 1px solid #e4ebe6; border-radius: 10px; padding: 10px 14px; }
.answer-pair summary { cursor: pointer; font-size: 14px; }
.answer-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 12px; }
.answer-grid p { white-space: pre-wrap; font-size: 13px; line-height: 1.6; color: #33403c; }
.citations { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }
.citation-link { color: #116b55; text-decoration: none; }
.citation-link:hover { text-decoration: underline; }
.citation-none { color: #9aa8a1; }

</style>
