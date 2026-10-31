<template>
  <div class="page">
    <div class="page-header">
      <h1>问题工厂</h1>
      <p class="muted">根据企业资料生成候选问题；人工编辑、删减并审核后形成固定版本，保证每次检测样本一致。</p>
    </div>

    <div class="toolbar">
      <label>当前企业：</label>
      <select v-model="businessId" @change="loadQuestionSets">
        <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
    </div>

    <!-- 生成候选问题 -->
    <section class="card">
      <h2>生成候选问题</h2>
      <div class="form-row">
        <label>生成模型</label>
        <select v-model="generator">
          <option v-for="p in configuredProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
      </div>
      <div class="form-row">
        <label>候选数量：<b>{{ count }}</b></label>
        <input type="range" v-model.number="count" min="3" max="30" />
      </div>
      <button class="btn primary" :disabled="generating" @click="generate">
        {{ generating ? '生成中…' : '生成候选问题' }}
      </button>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <!-- 问题集版本列表 -->
    <section class="card">
      <h2>问题集版本</h2>
      <table v-if="questionSets.length" class="table">
        <thead>
          <tr><th>版本</th><th>状态</th><th>问题数</th><th>生成时间</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="qs in questionSets" :key="qs.id">
            <td>v{{ qs.version }}</td>
            <td><span :class="['tag', statusClass(qs.status)]">{{ statusLabel(qs.status) }}</span></td>
            <td>{{ qs.question_count }}</td>
            <td>{{ displayTime(qs.created_at) }}</td>
            <td><button v-if="qs.status === 'draft'" class="btn" @click="openDraft(qs.id)">审核</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有问题集，先生成候选问题。</p>
    </section>

    <!-- 审核草稿 -->
    <section v-if="draft" class="card">
      <h2>审核草稿 v{{ draft.version }}</h2>
      <textarea v-model="reviewText" rows="12" class="textarea"></textarea>
      <p class="muted">每行一个问题；可用 Tab 分隔「问题」和「分类」。</p>
      <button class="btn primary" @click="approve">审核并启用此问题集</button>
    </section>

    <!-- 启动监测 -->
    <section v-if="activeSet" class="card">
      <h2>启动固定问题集检测</h2>
      <p class="muted">当前启用：v{{ activeSet.version }}，共 {{ activeSet.question_count }} 个问题</p>
      <div class="form-row">
        <label>检测阶段</label>
        <select v-model="stage">
          <option value="baseline">投喂前基线</option>
          <option value="after">投喂后复测</option>
        </select>
      </div>
      <div class="form-row">
        <label>检测模型</label>
        <div class="check-group">
          <label v-for="p in configuredProviders" :key="p.id" class="check">
            <input type="checkbox" :value="p.id" v-model="monitorProviders" /> {{ p.label }}
          </label>
        </div>
      </div>
      <button class="btn primary" :disabled="starting" @click="startMonitor">
        {{ starting ? '启动中…' : '启动检测' }}
      </button>

      <div v-if="started" class="success-banner">
        <span>✅ 检测已启动！监测在后台运行（约 4~5 分钟），完成后到「效果总览」查看结果。</span>
        <router-link to="/overview" class="btn primary">前往效果总览</router-link>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api, { getBusinesses, getProviders } from '../api'

const businesses = ref([])
const providers = ref([])
const businessId = ref(1)
const questionSets = ref([])
const generator = ref('deepseek')
const count = ref(6)
const generating = ref(false)
const error = ref('')
const draft = ref(null)
const reviewText = ref('')
const stage = ref('baseline')
const monitorProviders = ref([])
const starting = ref(false)
const started = ref(false)

const configuredProviders = computed(() => providers.value.filter(p => p.configured))
const activeSet = computed(() => questionSets.value.find(q => q.status === 'active'))

function statusLabel(s) { return { draft: '草稿', active: '启用中', archived: '已归档' }[s] || s }
function statusClass(s) { return { draft: 'draft', active: 'active', archived: 'archived' }[s] || '' }
function displayTime(v) { return v ? v.replace('T', ' ').slice(0, 16) : '—' }

async function loadQuestionSets() {
  try {
    const { data } = await api.get('/question-sets', { params: { business_id: businessId.value } })
    questionSets.value = data
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function generate() {
  generating.value = true
  error.value = ''
  try {
    await api.post('/question-sets/generate', {
      business_id: businessId.value,
      provider: generator.value,
      count: count.value,
    })
    await loadQuestionSets()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    generating.value = false
  }
}

async function openDraft(id) {
  try {
    const { data } = await api.get(`/question-sets/${id}`)
    draft.value = data
    reviewText.value = data.questions.map(q => `${q.text}\t${q.category || ''}`).join('\n')
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function approve() {
  const questions = reviewText.value.split('\n').map(line => {
    const tab = line.indexOf('\t')
    const text = tab >= 0 ? line.slice(0, tab) : line
    const category = tab >= 0 ? line.slice(tab + 1) : ''
    return { text: text.trim(), category: category.trim() }
  }).filter(q => q.text)
  if (!questions.length) { error.value = '至少保留一个问题'; return }
  try {
    await api.post(`/question-sets/${draft.value.id}/approve`, { questions })
    draft.value = null
    await loadQuestionSets()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function startMonitor() {
  if (!monitorProviders.value.length) { error.value = '请至少选择一个模型'; return }
  starting.value = true
  error.value = ''
  try {
    await api.post('/monitor-runs', {
      business_id: businessId.value,
      stage: stage.value,
      providers: monitorProviders.value,
      question_set_id: activeSet.value.id,
    })
    started.value = true
    monitorProviders.value = []
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    starting.value = false
  }
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    providers.value = await getProviders()
    if (businesses.value.length) businessId.value = businesses.value[0].id
    if (configuredProviders.value.length) {
      generator.value = configuredProviders.value[0].id
      monitorProviders.value = [configuredProviders.value[0].id]
    }
    await loadQuestionSets()
  } catch (e) {
    error.value = e.message
  }
})
</script>

<style scoped>
.card { background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 22px 24px; }
.card h2 { margin: 0 0 16px; font-size: 17px; }
.form-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.form-row label { font-size: 14px; color: #70817b; min-width: 80px; }
select, textarea { padding: 8px 12px; border: 1px solid #e4ebe6; border-radius: 8px; font-size: 14px; background: #fff; }
textarea { width: 100%; font-family: inherit; }
input[type="range"] { flex: 1; }
.btn { padding: 9px 16px; border: 1px solid #e4ebe6; border-radius: 9px; background: #fff; cursor: pointer; font-size: 14px; text-decoration: none; }
.btn.primary { background: #116b55; color: #fff; border-color: #116b55; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.tag.draft { background: #fff7e6; color: #b45309; }
.tag.active { background: #e9f5ef; color: #116b55; }
.tag.archived { background: #f1f5f9; color: #64748b; }
.check-group { display: flex; gap: 16px; flex-wrap: wrap; }
.check { display: flex; align-items: center; gap: 6px; font-size: 14px; }
.error { color: #b91c1c; font-size: 13px; margin-top: 10px; }
.success-banner { background: #e9f5ef; border: 1px solid #b7dcc8; border-radius: 12px;
  padding: 14px 16px; color: #116b55; font-size: 14px; display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 16px; }
</style>
