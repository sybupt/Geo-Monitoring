<template>
  <div class="page">
    <div class="hero">
      <div class="eyebrow">AI Search Visibility · GEO Intelligence</div>
      <h1>{{ business?.name || '—' }} 的 AI 搜索表现</h1>
      <p>{{ business?.region }} · {{ business?.category }} ｜ 追踪多个大模型对真实用户问题的回答与品牌提及</p>
    </div>

    <div class="toolbar">
      <label>当前企业：</label>
      <select v-model="businessId" @change="load">
        <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
    </div>

    <div class="metrics">
      <div class="metric-card">
        <div class="metric-label">投喂后平均提及率</div>
        <div class="metric-value">{{ displayRate(meanPostRate) }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">较投喂前变化</div>
        <div class="metric-value">{{ meanChange != null ? `${meanChange >= 0 ? '+' : ''}${meanChange.toFixed(1)} pp` : '待建立基线' }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">已接入模型</div>
        <div class="metric-value">{{ configuredCount }} / {{ providers.length }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">监测状态</div>
        <div class="metric-value">{{ overview?.active_runs ? `${overview.active_runs} 个运行中` : '运行正常' }}</div>
      </div>
    </div>

    <section class="section">
      <h2>模型可见性对比</h2>
      <table class="table">
        <thead>
          <tr><th>模型</th><th>投喂前提及率</th><th>投喂后提及率</th><th>变化</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in overview?.comparisons || []" :key="c.provider">
            <td>{{ c.provider_name }}</td>
            <td>{{ displayRate(c.baseline?.mention_rate) }}</td>
            <td>{{ displayRate(c.after?.mention_rate) }}</td>
            <td>{{ c.change != null ? `${c.change >= 0 ? '+' : ''}${c.change.toFixed(1)} pp` : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getBusinesses, getProviders, getOverview } from '../api'

const businesses = ref([])
const providers = ref([])
const overview = ref(null)
const businessId = ref(1)

const business = computed(() => businesses.value.find(b => b.id === businessId.value))
const configuredCount = computed(() => providers.value.filter(p => p.configured).length)
const meanPostRate = computed(() => {
  const rates = (overview.value?.comparisons || []).filter(c => c.after?.mention_rate != null).map(c => c.after.mention_rate)
  return rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : null
})
const meanChange = computed(() => {
  const changes = (overview.value?.comparisons || []).filter(c => c.change != null).map(c => c.change)
  return changes.length ? changes.reduce((a, b) => a + b, 0) / changes.length : null
})

function displayRate(v) { return v != null ? `${v.toFixed(1)}%` : '—' }

async function load() {
  try { overview.value = await getOverview(businessId.value) } catch (e) { console.error(e) }
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    providers.value = await getProviders()
    if (businesses.value.length) businessId.value = businesses.value[0].id
    await load()
  } catch (e) { console.error(e) }
})
</script>
