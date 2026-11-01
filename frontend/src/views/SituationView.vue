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
        <div class="kpi-icon" :style="{ background: k.bg }">{{ k.icon }}</div>
        <div>
          <div class="kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
          <div class="kpi-label">{{ k.label }}</div>
        </div>
      </div>
    </div>

    <section class="card">
      <h2>📈 提及率趋势</h2>
      <div ref="trendRef" class="chart"></div>
      <p v-if="!hasTrend" class="muted">暂无监测记录</p>
    </section>

    <div class="grid2">
      <section class="card">
        <h2>⚖️ 模型对比</h2>
        <div ref="modelRef" class="chart"></div>
      </section>
      <section class="card">
        <h2>📡 信源分布</h2>
        <div ref="sourceRef" class="chart"></div>
      </section>
    </div>

    <section class="card">
      <h2>🔔 最新监测动态</h2>
      <ul class="run-list">
        <li v-for="r in data.latest_runs" :key="r.run_id">
          <span class="tag" :class="r.stage === 'after' ? 'active' : ''">{{ r.stage === 'after' ? '复测' : '基线' }}</span>
          <span>🤖 {{ r.provider_name }}</span>
          <span class="muted">{{ displayTime(r.time) }}</span>
          <span class="run-rate">{{ r.mention_rate != null ? r.mention_rate + '%' : '—' }}</span>
        </li>
      </ul>
      <p v-if="!data.latest_runs || !data.latest_runs.length" class="muted">暂无监测记录</p>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import api, { getBusinesses } from '../api'

const businesses = ref([])
const scopeId = ref(0)
const data = ref({})

const trendRef = ref(null)
const modelRef = ref(null)
const sourceRef = ref(null)
let trendChart, modelChart, sourceChart

const kpiCards = computed(() => {
  const k = data.value.kpi || {}
  return [
    { label: '监测企业', value: k.business_count ?? 0, icon: '🏢', bg: '#e9f5ef', color: '#116b55' },
    { label: '接入模型', value: k.model_count ?? 0, icon: '🤖', bg: '#eef2ff', color: '#4f46e5' },
    { label: '监测问题', value: k.question_count ?? 0, icon: '📝', bg: '#fff7e6', color: '#b45309' },
    { label: '当前提及率', value: k.latest_mention_rate != null ? k.latest_mention_rate + '%' : '—', icon: '📈', bg: '#f5f3ff', color: '#7c3aed' },
    { label: '总引用次数', value: k.total_citations ?? 0, icon: '🔗', bg: '#e0f2fe', color: '#0e7490' },
  ]
})
const hasTrend = computed(() => data.value.mention_trend && data.value.mention_trend.length > 0)

function displayTime(v) { return v ? v.replace('T', ' ').slice(0, 16) : '—' }

function renderTrend() {
  if (!trendChart) return
  const trend = data.value.mention_trend || []
  const labels = trend.map((t, i) => `#${t.run_id}`)
  const providers = [...new Set(trend.map(t => t.provider_name))]
  const colors = ['#116b55', '#4f46e5', '#b45309', '#7c3aed', '#0e7490']
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: providers, top: 0 },
    grid: { left: 40, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: providers.map((p, i) => ({
      name: p,
      type: 'line',
      smooth: true,
      connectNulls: true,
      lineStyle: { width: 2 },
      itemStyle: { color: colors[i % colors.length] },
      data: trend.map(t => (t.provider_name === p ? t.mention_rate : null)),
    })),
  })
}

function renderModels() {
  if (!modelChart) return
  const models = data.value.models || []
  modelChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 40, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: models.map(m => m.label) },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{
      type: 'bar',
      data: models.map(m => m.mention_rate),
      itemStyle: { color: '#116b55', borderRadius: [6, 6, 0, 0] },
      barMaxWidth: 60,
      label: { show: true, position: 'top', formatter: '{c}%' },
    }],
  })
}

function renderSources() {
  if (!sourceChart) return
  const sources = [...(data.value.sources || [])].reverse()
  sourceChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 40, top: 10, bottom: 20 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: sources.map(s => s.platform_label) },
    series: [{
      type: 'bar',
      data: sources.map(s => s.total),
      itemStyle: { color: '#2fae7e', borderRadius: [0, 6, 6, 0] },
      barMaxWidth: 20,
      label: { show: true, position: 'right', color: '#70817b' },
    }],
  })
}

function renderCharts() {
  renderTrend()
  renderModels()
  renderSources()
}

async function load() {
  const params = scopeId.value === 0 ? {} : { business_id: scopeId.value }
  try {
    const res = await api.get('/situation/overview', { params })
    data.value = res.data
    await nextTick()
    renderCharts()
  } catch (e) {
    data.value = {}
  }
}

function onResize() {
  trendChart?.resize()
  modelChart?.resize()
  sourceChart?.resize()
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    await load()
    trendChart = echarts.init(trendRef.value)
    modelChart = echarts.init(modelRef.value)
    sourceChart = echarts.init(sourceRef.value)
    renderCharts()
    window.addEventListener('resize', onResize)
  } catch (e) {}
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  trendChart?.dispose()
  modelChart?.dispose()
  sourceChart?.dispose()
})
</script>

<style scoped>
.page { padding: 24px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; color: #182d2a; }
.muted { color: #70817b; font-size: 14px; }
.toolbar { margin: 16px 0; font-size: 14px; }
.toolbar select { padding: 6px 12px; border: 1px solid #e4ebe6; border-radius: 8px; margin-left: 6px; }

.kpi-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 20px; }
.kpi-card {
  background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 18px;
  display: flex; align-items: center; gap: 14px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  transition: transform .15s, box-shadow .15s;
}
.kpi-card:hover { transform: translateY(-3px); box-shadow: 0 6px 16px rgba(0,0,0,0.08); }
.kpi-icon {
  width: 50px; height: 50px; border-radius: 12px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 24px;
}
.kpi-value { font-size: 26px; font-weight: 700; line-height: 1.1; }
.kpi-label { color: #70817b; font-size: 13px; margin-top: 4px; }

.card {
  background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 20px 22px;
  margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.03);
}
.card h2 { margin: 0 0 16px; font-size: 16px; color: #182d2a; }
.chart { width: 100%; height: 300px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid2 .card { margin-bottom: 16px; }

.run-list { list-style: none; padding: 0; margin: 0; }
.run-list li { display: flex; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid #f0f5f1; font-size: 14px; }
.run-rate { margin-left: auto; font-weight: 700; color: #116b55; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; background: #eef2ff; color: #4f46e5; }
.tag.active { background: #e9f5ef; color: #116b55; }
</style>
