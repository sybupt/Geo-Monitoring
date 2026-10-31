<template>
  <div class="page">
    <div class="page-header">
      <h1>会员管理</h1>
      <p class="muted">管理企业会员、套餐分配和缴费状态（仅团队可见）。</p>
    </div>

    <div class="tabs">
      <button :class="['tab', tab === 'businesses' && 'active']" @click="tab = 'businesses'">企业管理</button>
      <button :class="['tab', tab === 'plans' && 'active']" @click="tab = 'plans'">套餐与平台</button>
    </div>

    <!-- ============ 企业管理 ============ -->
    <section v-if="tab === 'businesses'" class="card">
      <div class="card-head">
        <h2>企业会员</h2>
        <button class="btn primary" @click="showForm = !showForm">{{ showForm ? '收起' : '新建企业' }}</button>
      </div>

      <!-- 新建/编辑表单 -->
      <div v-if="showForm" class="form-panel">
        <div class="form-grid">
          <div class="form-row"><label>企业名称 *</label><input v-model="form.name" class="input" /></div>
          <div class="form-row"><label>地区</label><input v-model="form.region" class="input" /></div>
          <div class="form-row"><label>行业类别</label><input v-model="form.category" class="input" /></div>
          <div class="form-row"><label>品牌别名</label><input v-model="form.alias" class="input" /></div>
          <div class="form-row"><label>套餐</label>
            <select v-model="form.plan_key" class="input">
              <option v-for="p in plans" :key="p.key" :value="p.key">{{ p.label }}（¥{{ p.annual_price }}）</option>
            </select></div>
          <div class="form-row"><label>缴费状态</label>
            <select v-model="form.subscription_status" class="input">
              <option value="trial">试用中</option>
              <option value="active">正常</option>
              <option value="expired">已过期</option>
              <option value="suspended">已停用</option>
            </select></div>
          <div class="form-row"><label>到期日期</label><input type="date" v-model="form.subscription_expires_at" class="input" /></div>
          <div class="form-row"><label>联系人</label><input v-model="form.contact_name" class="input" /></div>
          <div class="form-row"><label>联系电话</label><input v-model="form.contact_phone" class="input" /></div>
          <div class="form-row"><label>联系邮箱</label><input v-model="form.contact_email" class="input" /></div>
        </div>
        <button class="btn primary" @click="saveBusiness">{{ editingId ? '保存修改' : '创建企业' }}</button>
        <button v-if="editingId" class="btn" @click="resetForm">取消</button>
        <p v-if="error" class="error">{{ error }}</p>
      </div>

      <!-- 企业列表 -->
      <table class="table">
        <thead>
          <tr><th>企业</th><th>地区</th><th>套餐</th><th>缴费状态</th><th>到期时间</th><th>联系人</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in businesses" :key="b.id">
            <td>{{ b.name }}</td>
            <td>{{ b.region || '—' }}</td>
            <td>{{ planLabel(b.plan_key) }}</td>
            <td><span :class="['tag', statusClass(b.subscription_status)]">{{ statusLabel(b.subscription_status) }}</span></td>
            <td>{{ displayDate(b.subscription_expires_at) }}</td>
            <td>{{ b.contact_name || '—' }}</td>
            <td><button class="btn small" @click="editBusiness(b)">编辑</button></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!businesses.length" class="muted">还没有企业会员。</p>
    </section>

    <!-- ============ 套餐与平台 ============ -->
    <section v-else class="card">
      <h2>套餐</h2>
      <table class="table">
        <thead><tr><th>套餐</th><th>年费</th><th>包含平台</th></tr></thead>
        <tbody>
          <tr v-for="p in plans" :key="p.key">
            <td>{{ p.label }}</td>
            <td>¥{{ p.annual_price }}</td>
            <td>{{ p.platform_keys.map(k => platformLabel(k)).join('、') }}</td>
          </tr>
        </tbody>
      </table>

      <h2 style="margin-top:24px">平台评分</h2>
      <table class="table">
        <thead><tr><th>平台</th><th>权威度</th><th>发布难度</th><th>综合分</th></tr></thead>
        <tbody>
          <tr v-for="p in platforms" :key="p.key">
            <td>{{ p.label }}</td>
            <td>{{ p.authority }}</td>
            <td>{{ p.difficulty }}</td>
            <td><b>{{ p.score }}</b></td>
          </tr>
        </tbody>
      </table>
      <p class="muted">综合分 = 权威度 × 0.6 + 发布难度 × 0.4</p>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api, { getBusinesses } from '../api'

const tab = ref('businesses')
const businesses = ref([])
const plans = ref([])
const platforms = ref([])
const error = ref('')
const showForm = ref(false)
const editingId = ref(null)

const form = ref({})

function emptyForm() {
  return { name: '', region: '', category: '', alias: '', plan_key: 'trial',
    subscription_status: 'trial', subscription_expires_at: '', contact_name: '', contact_phone: '', contact_email: '' }
}

function resetForm() { form.value = emptyForm(); editingId.value = null; showForm.value = false }

function planLabel(key) { return plans.value.find(p => p.key === key)?.label || key }
function platformLabel(key) { return platforms.value.find(p => p.key === key)?.label || key }
function statusLabel(s) { return { trial: '试用中', active: '正常', expired: '已过期', suspended: '已停用' }[s] || s }
function statusClass(s) { return { trial: 'trial', active: 'active', expired: 'expired', suspended: 'suspended' }[s] || '' }
function displayDate(v) { return v ? v.slice(0, 10) : '—' }

async function loadAll() {
  try {
    const [b, p, pl] = await Promise.all([
      api.get('/businesses'), api.get('/plans'), api.get('/platforms'),
    ])
    businesses.value = b.data
    plans.value = p.data
    platforms.value = pl.data
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

function editBusiness(b) {
  editingId.value = b.id
  form.value = {
    name: b.name || '',
    region: b.region || '',
    category: b.category || '',
    alias: b.alias || '',
    plan_key: b.plan_key || 'trial',
    subscription_status: b.subscription_status || 'trial',
    subscription_expires_at: b.subscription_expires_at ? b.subscription_expires_at.slice(0, 10) : '',
    contact_name: b.contact_name || '',
    contact_phone: b.contact_phone || '',
    contact_email: b.contact_email || '',
    description: b.description || '',
    selling_points: b.selling_points || '',
    evidence: b.evidence || '',
    website: b.website || '',
  }
  showForm.value = true
}


async function saveBusiness() {
  if (!form.value.name?.trim()) { error.value = '请填写企业名称'; return }
  error.value = ''
  const payload = {}
  for (const [k, v] of Object.entries(form.value)) {
    payload[k] = v == null ? '' : v
  }
  try {
    if (editingId.value) {
      await api.put(`/businesses/${editingId.value}`, payload)
    } else {
      await api.post('/businesses', payload)
    }
    resetForm()
    await loadAll()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}


onMounted(() => { resetForm(); loadAll() })
</script>

<style scoped>
.card { background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 22px 24px; margin-bottom: 20px; }
.card h2 { margin: 0 0 16px; font-size: 17px; }
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.tabs { display: flex; gap: 8px; margin-bottom: 20px; }
.tab { padding: 9px 18px; border: 1px solid #e4ebe6; border-radius: 9px; background: #fff; cursor: pointer; font-size: 14px; }
.tab.active { background: #116b55; color: #fff; border-color: #116b55; }
.form-panel { border: 1px solid #e4ebe6; border-radius: 12px; padding: 18px; margin-bottom: 20px; background: #f8faf8; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-row { display: flex; align-items: center; gap: 10px; }
.form-row label { font-size: 14px; color: #70817b; min-width: 80px; }
.input { padding: 8px 12px; border: 1px solid #e4ebe6; border-radius: 8px; font-size: 14px; flex: 1; background: #fff; }
.btn { padding: 9px 16px; border: 1px solid #e4ebe6; border-radius: 9px; background: #fff; cursor: pointer; font-size: 14px; }
.btn.primary { background: #116b55; color: #fff; border-color: #116b55; }
.btn.small { padding: 5px 10px; font-size: 13px; }
.table { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 8px; }
.table th, .table td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #e4ebe6; }
.table th { background: #f0f5f1; color: #116b55; font-weight: 600; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.tag.trial { background: #eef2ff; color: #4f46e5; }
.tag.active { background: #e9f5ef; color: #116b55; }
.tag.expired { background: #fee2e2; color: #b91c1c; }
.tag.suspended { background: #f1f5f9; color: #64748b; }
.error { color: #b91c1c; font-size: 13px; margin-top: 10px; }
</style>
