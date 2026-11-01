<template>
  <div class="page">
    <div class="page-header">
      <h1>态势展示</h1>
      <p class="muted">GEO 监测驾驶舱，一屏掌握全局。</p>
    </div>

    <div class="toolbar">
      <label>范围：</label>
      <select v-model="scopeId" @change="load">
        <option :value="0">全部企业</option>
        <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
    </div>

    <div class="kpi-row">
      <div class="kpi-card" v-for="k in kpiCards" :key="k.label">
        <div class="kpi-value">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
      </div>
    </div>

    <div class="grid2">
      <section class="card">
        <h2>提及率趋势</h2>
        <div v-if="data.mention_trend && data.mention_trend.length" class="trend-chart">
          <div v-for="t in data.mention_trend" :key="t.run_id" class="trend-bar"
               :title="`#${t.run_id} ${t.provider_name} ${t.mention_rate}%`">
            <div class="bar-fill" :style="{ height: t.mention_rate + '%' }"></div>
          </div>
        </div>
        <p v-else class="muted">暂无监测记录</p>
      </section>

      <section class="card">
        <h2>模型对比</h2>
        <div v-for="m in data.models" :key="m.provider" class="model-row">
          <div class="row-head"><span>{{ m.label }}</span><span>{{ m.mention_rate }}%</span></div>
          <div class="progress"><div class="progress-fill" :style="{ width: m.mention_rate + '%' }"></div></div>
          <div class="row-meta">监测 {{ m.total }} · 提及 {{ m.mentioned }} · 引用 {{ m.citations }}</div>
        </div>
        <p v-if="!data.models || !data.models.length" class="muted">暂无模型数据</p>
      </section>
    </div>

    <div class="grid2">
      <section class="card">
        <h2>信源分布</h2>
        <div v-for="s in data.sources" :key="s.platform_key" class="source-row">
          <span class="source-name">{{ s.platform_label }}</span>
          <div class="progress"><div class="progress-fill" :style="{ width: sourceWidth(s.total) }"></div></div>
          <span class="source-count">{{ s.total }}</span>
        </div>
        <p v-if="!data.sources || !data.sources.length" class="muted">暂无引用数据</p>
      </section>

      <section class="card">
        <h2>最新监测动态</h2>
        <ul class="run-list">
          <li v-for="r in data.latest_runs" :key="r.run_id">
            <span class="tag" :class="r.stage === 'after' ? 'active' : ''">{{ r.stage === 'after' ? '复测' : '基线' }}</span>
            <span>{{ r.provider_name }}</span>
            <span class="muted">{{ displayTime(r.time) }}</span>
            <span class="run-rate">{{ r.mention_rate != null ? r.mention_rate + '%' : '—' }}</span>
          </li>
        </ul>
        <p v-if="!data.latest_runs || !data.latest_runs.length" class="muted">暂无监测记录</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api, { getBusinesses } from '../api'

const businesses = ref([])
const scopeId = ref(0)
const data = ref({})

const kpiCards = computed(() => {
  const k = data.value.kpi || {}
  return [
    { label: '监测企业', value: k.business_count ?? 0 },
    { label: '接入模型', value: k.model_count ?? 0 },
    { label: '监测问题', value: k.question_count ?? 0 },
    { label: '当前提及率', value: k.latest_mention_rate != null ? k.latest_mention_rate + '%' : '—' },
    { label: '总引用次数', value: k.total_citations ?? 0 },
  ]
})

function displayTime(v) { return v ? v.replace('T', ' ').slice(0, 16) : '—' }
function sourceWidth(total) {
  const max = Math.max(...(data.value.sources || []).map(s => s.total), 1)
  return (total / max * 100) + '%'
}

async function load() {
  const params = scopeId.value === 0 ? {} : { business_id: scopeId.value }
  try {
    const res = await api.get('/situation/overview', { params })
    data.value = res.data
  } catch (e) {
    data.value = {}
  }
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    await load()
  } catch (e) {}
})
</script>

<style scoped>
.page { padding: 24px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; color: #182d2a; }
.muted { color: #70817b; font-size: 14px; }
.toolbar { margin: 16px 0; font-size: 14px; }
.toolbar select { padding: 6px 12px; border: 1px solid #e4ebe6; border-radius: 8px; margin-left: 6px; }
.kpi-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 20px; }
.kpi-card { background: #fff; border: 1px solid #e4ebe6; border-radius: 12px; padding: 18px; }
.kpi-value { font-size: 26px; font-weight: 700; color: #116b55; }
.kpi-label { color: #70817b; font-size: 13px; margin-top: 6px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.card { background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 20px 22px; }
.card h2 { margin: 0 0 14px; font-size: 16px; }
.trend-chart { display: flex; align-items: flex-end; gap: 6px; height: 160px; }
.trend-bar { flex: 1; display: flex; align-items: flex-end; height: 100%; background: #f6f9f7; border-radius: 4px; }
.bar-fill { width: 100%; background: #116b55; border-radius: 4px; min-height: 2px; }
.model-row, .source-row { margin-bottom: 12px; }
.row-head { display: flex; justify-content: space-between; font-size: 14px; margin-bottom: 4px; }
.row-meta { font-size: 12px; color: #70817b; margin-top: 4px; }
.progress { height: 8px; background: #eef3f0; border-radius: 999px; overflow: hidden; }
.progress-fill { height: 100%; background: #116b55; border-radius: 999px; }
.source-row { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.source-name { width: 80px; flex-shrink: 0; }
.source-count { width: 30px; text-align: right; color: #70817b; }
.run-list { list-style: none; padding: 0; margin: 0; }
.run-list li { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid #f0f5f1; font-size: 14px; }
.run-rate { margin-left: auto; font-weight: 600; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; background: #eef2ff; color: #4f46e5; }
.tag.active { background: #e9f5ef; color: #116b55; }
</style>
