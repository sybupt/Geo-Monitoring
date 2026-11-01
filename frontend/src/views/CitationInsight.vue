<template>
  <div class="page">
    <div class="page-header">
      <h1>引用洞察</h1>
      <p class="muted">透视 AI 模型引用了哪些信源平台，识别投喂效果与机会缺口。</p>
    </div>

    <div class="toolbar">
      <label>当前企业：</label>
        <select v-model="businessId" @change="loadAll; loadModelComparison">
        <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
    </div>

    <div class="tabs">
      <button v-for="t in tabs" :key="t.key"
              :class="['tab', { active: activeTab === t.key }]"
              @click="activeTab = t.key">{{ t.label }}</button>
    </div>

    <!-- 信源聚合 -->
    <section v-if="activeTab === 'source'" class="card">
      <div class="filters">
        <label>状态：</label>
        <select v-model="statusFilter" class="input">
          <option value="">全部</option>
          <option value="new">本期新增</option>
          <option value="sustained">持续被引</option>
          <option value="lost">本期流失</option>
        </select>
      </div>

      <table v-if="filteredRows.length" class="table">
        <thead>
          <tr>
            <th>信源平台</th>
            <th>状态</th>
            <th v-for="p in overview.providers" :key="p.key">{{ p.label }}</th>
            <th>合计</th>
            <th>较上期</th>
            <th>投喂</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in filteredRows" :key="r.platform_key">
            <td>{{ r.platform_label }}</td>
            <td><span :class="['tag', statusClass(r.status)]">{{ statusText(r.status) }}</span></td>
            <td v-for="p in overview.providers" :key="p.key">{{ r.counts[p.key] || 0 }}</td>
            <td>{{ r.total }}</td>
            <td :class="changeClass(r.change)">{{ changeText(r.change) }}</td>
            <td>{{ investmentText(r.investment) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无引用数据。</p>
    </section>

    <!-- 模型对比 -->
    <section v-else-if="activeTab === 'model'" class="card">
      <table v-if="modelComparison.length" class="table">
        <thead>
          <tr><th>模型</th><th>监测问题数</th><th>提及数</th><th>提及率</th><th>引用次数</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in modelComparison" :key="m.provider">
            <td>{{ m.label }}</td>
            <td>{{ m.total }}</td>
            <td>{{ m.mentioned }}</td>
            <td>{{ m.mention_rate }}%</td>
            <td>{{ m.citations }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无监测数据。</p>
    </section>

    <!-- 其余两个视图骨架 -->
    <section v-else class="card">
      <p class="muted">「{{ tabLabel }}」视图规划中，待引用数据接入后上线。</p>
    </section>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api, { getBusinesses } from '../api'

const tabs = [
  { key: 'source', label: '信源聚合' },
  { key: 'model', label: '模型对比' },
  { key: 'article', label: '引用文章' },
  { key: 'attribution', label: '归因' },
]

const businesses = ref([])
const businessId = ref(1)
const activeTab = ref('source')
const statusFilter = ref('')
const overview = ref({ providers: [], rows: [] })
const modelComparison = ref([])


const filteredRows = computed(() => {
  if (!statusFilter.value) return overview.value.rows
  return overview.value.rows.filter(r => r.status === statusFilter.value)
})
const tabLabel = computed(() => tabs.find(t => t.key === activeTab.value)?.label || '')

function statusText(s) { return { new: '本期新增', sustained: '持续被引', lost: '本期流失', none: '—' }[s] || '—' }
function statusClass(s) { return { new: 'active', sustained: 'active', lost: 'draft' }[s] || '' }
function changeText(v) { return v == null ? '—' : `${v > 0 ? '▲ +' : v < 0 ? '▼ ' : ''}${v}` }
function changeClass(v) { return v == null ? '' : v > 0 ? 'change-up' : v < 0 ? 'change-down' : '' }
function investmentText(k) { return { fed: '✅ 已投喂', owned_unfed: '⚪ 已购未投', gap: '🔶 机会缺口' }[k] || '—' }

async function loadAll() {
  try {
    const { data } = await api.get('/citation-insight/source-aggregation', { params: { business_id: businessId.value } })
    overview.value = data
  } catch (e) {
    overview.value = { providers: [], rows: [] }
  }
}

async function loadModelComparison() {
  try {
    const { data } = await api.get('/citation-insight/model-comparison', { params: { business_id: businessId.value } })
    modelComparison.value = data.models || []
  } catch (e) {
    modelComparison.value = []
  }
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    if (businesses.value.length) businessId.value = businesses.value[0].id
    await loadAll()
    await loadModelComparison()

  } catch (e) {}
})
</script>

<style scoped>
.page { padding: 24px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; color: #182d2a; }
.muted { color: #70817b; font-size: 14px; }
.toolbar { margin: 16px 0; font-size: 14px; }
.toolbar select { padding: 6px 12px; border: 1px solid #e4ebe6; border-radius: 8px; margin-left: 6px; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tab { padding: 8px 18px; border: 1px solid #e4ebe6; border-radius: 999px; background: #fff; cursor: pointer; font-size: 14px; color: #33403c; }
.tab.active { background: #116b55; color: #fff; border-color: #116b55; }
.card { background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 22px 24px; margin-bottom: 20px; }
.filters { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.input { padding: 8px 12px; border: 1px solid #e4ebe6; border-radius: 8px; font-size: 14px; }
.table { width: 100%; border-collapse: collapse; font-size: 14px; }
.table th, .table td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #e4ebe6; }
.table th { background: #f0f5f1; color: #116b55; font-weight: 600; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.tag.active { background: #e9f5ef; color: #116b55; }
.tag.draft { background: #fff7e6; color: #b45309; }
.change-up { color: #16a34a; font-weight: 700; }
.change-down { color: #b91c1c; font-weight: 700; }
</style>
