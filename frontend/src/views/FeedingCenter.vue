<template>
  <div class="page">
    <div class="page-header">
      <h1>投喂中心</h1>
      <p class="muted">根据企业套餐自动匹配平台；生成文案后，可预览发布效果、复制、跳转发布，完成后回来登记公开链接。</p>
    </div>

    <div class="toolbar">
      <label>当前企业：</label>
      <select v-model="businessId" @change="onBusinessChange">
        <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
      <span v-if="planInfo" class="muted">当前套餐：{{ planInfo.plan_label }}</span>
    </div>

    <!-- 创建投喂计划 -->
    <section class="card">
      <h2>创建投喂计划</h2>
      <div class="form-grid">
        <div class="form-row"><label>计划名称</label>
          <input v-model="plan.name" class="input" placeholder="例如：恩施菜馆宣传计划" /></div>
        <div class="form-row"><label>业务关键词</label>
          <input v-model="plan.business_keywords" class="input" placeholder="例如：恩施土家菜、亲子餐厅" /></div>
        <div class="form-row"><label>活动名称</label>
          <input v-model="plan.activity_name" class="input" placeholder="可选" /></div>
        <div class="form-row"><label>宣传点</label>
          <textarea v-model="plan.promotional_points" rows="3" class="textarea" placeholder="真实服务、产品或亮点"></textarea></div>
        <div class="form-row"><label>事件时间</label>
          <input v-model="plan.event_time" class="input" placeholder="可选，如 2026年10月18日" /></div>
        <div class="form-row"><label>事件地点</label>
          <input v-model="plan.event_location" class="input" placeholder="可选" /></div>
        <div class="form-row"><label>事件描述</label>
          <textarea v-model="plan.event_description" rows="3" class="textarea" placeholder="可选，只填写已确认事实"></textarea></div>
        <div class="form-row"><label>文案模型</label>
          <select v-model="plan.provider" class="input">
            <option v-for="p in configuredProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
          </select></div>
        <div class="form-row"><label>目标平台</label>
          <div class="check-group">
            <label v-for="(label, key) in allowedChannels" :key="key" class="check">
              <input type="checkbox" :value="key" v-model="plan.channels" /> {{ label }}
            </label>
          </div></div>
      </div>
      <button class="btn primary" :disabled="creating" @click="createPlan">
        {{ creating ? '生成中…（约 1~2 分钟）' : '生成平台文案' }}
      </button>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <!-- 投喂计划列表 -->
    <section v-for="p in plans" :key="p.id" class="card">
      <div class="plan-header">
        <h2>{{ p.name }}</h2>
        <span :class="['tag', planStatusClass(p.status)]">{{ planStatusLabel(p.status) }}</span>
      </div>
      <p v-if="p.campaign_input" class="muted">本计划输入：{{ campaignSummary(p.campaign_input) }}</p>

      <div v-for="asset in p.assets" :key="asset.id" class="asset">
        <div class="asset-header">
          <b>{{ asset.channel_name }}</b>
          <span :class="['tag', assetStatusClass(asset.status)]">{{ assetStatusLabel(asset.status) }}</span>
        </div>

        <div v-if="asset.status === 'published'">
          <p class="asset-title">{{ asset.title }}</p>
          <div class="asset-content">{{ asset.content }}</div>
          <p class="muted">已发布素材不可再编辑。公开链接：{{ asset.published_url || '未记录' }}</p>
        </div>

        <template v-else>
          <div class="form-row"><label>标题</label><input v-model="asset.title" class="input" /></div>
          <div class="form-row"><label>正文</label><textarea v-model="asset.content" rows="8" class="textarea"></textarea></div>
          <button class="btn" @click="saveAsset(asset)">保存文案</button>

          <div class="images">
            <div v-for="img in asset.images" :key="img.id" class="image-item">
              <img :src="img.url" :alt="img.alt_text || img.filename" class="thumb" />
              <button class="btn small" @click="deleteImage(asset, img.id)">移除</button>
            </div>
          </div>
          <label class="btn small upload">
            添加图片
            <input type="file" accept="image/jpeg,image/png,image/webp" hidden @change="uploadImage(asset, $event)" />
          </label>

          <div class="asset-actions">
            <button class="btn" v-if="asset.status === 'draft'" @click="reviewAsset(asset)">审核通过</button>
            <template v-if="asset.status === 'reviewed'">
              <input v-model="asset.publishUrl" class="input" placeholder="发布后公开链接 https://..." />
              <button class="btn primary" @click="publishAsset(asset)">记录已发布</button>
            </template>
          </div>
        </template>

        <!-- 正式发布效果预览 -->
        <div v-if="previewingId === asset.id" class="publish-preview">
          <div class="preview-bar" :style="{ background: themeColor(asset.channel) }">
            <span>{{ asset.channel_name }}</span>
            <span class="preview-badge">正式发布效果</span>
          </div>
          <div class="preview-body">
            <h1 class="preview-title">{{ asset.title }}</h1>
            <div class="preview-images" v-if="asset.images.length">
              <img v-for="img in asset.images" :key="img.id" :src="img.url" class="preview-img" />
            </div>
            <div class="preview-content" v-html="renderMarkdown(asset.content)"></div>
          </div>
        </div>

        <div class="guide-bar">
          <button class="btn" @click="togglePreview(asset.id)">
            {{ previewingId === asset.id ? '收起预览' : '👁 预览发布效果' }}
          </button>
          <button class="btn" @click="copyAsset(asset)">{{ copiedId === asset.id ? '✅ 已复制' : '📋 复制文案' }}</button>
          <a v-if="PLATFORM_URLS[asset.channel]" :href="PLATFORM_URLS[asset.channel]" target="_blank" rel="noopener" class="btn">
            跳转{{ CHANNELS[asset.channel] }}发布 ↗
          </a>
<span v-else-if="asset.channel === 'wechat_personal'" class="hint">📱 请在微信朋友圈发布</span>
<template v-else>
  <a v-for="pp in PARTNER_PLATFORMS" :key="pp.key" :href="pp.url" target="_blank" rel="noopener" class="btn">
    跳转{{ pp.name }} ↗
  </a>
</template>

          <a class="btn" :href="`/api/feeding-assets/${asset.id}/download`" download>下载素材包</a>
        </div>
      </div>

      <p v-if="!p.assets.length" class="muted">该计划在当前套餐下没有可展示的平台素材。</p>
    </section>

    <p v-if="!plans.length && loaded" class="muted">还没有投喂计划。先在上方创建计划。</p>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { marked } from 'marked'
import api, { getBusinesses, getProviders } from '../api'

const CHANNELS = {
  wechat_personal: '微信个人号',
  wechat: '微信公众号',
  official_site: '企业官网',
  rednote: '小红书',
  zhihu: '知乎',
  baidu: '百度（百家号）',
  tourism_bureau: '文旅局',
  government: '政府平台',
}

const PLATFORM_URLS = {
  rednote: 'https://creator.xiaohongshu.com',
  zhihu: 'https://zhuanlan.zhihu.com/write',
  baidu: 'https://baijiahao.baidu.com',
  wechat: 'https://mp.weixin.qq.com',
}

const PARTNER_PLATFORMS = [
  { key: 'partner_wl', name: '恩施州文旅局官网', url: 'https://example.gov.cn/wl' },
  { key: 'partner_gov', name: '恩施市政府网站', url: 'https://example.gov.cn/gov' },
]

const CHANNEL_THEMES = {
  wechat_personal: '#07c160',
  wechat: '#07c160',
  official_site: '#116b55',
  rednote: '#ff2442',
  zhihu: '#0066ff',
  baidu: '#2932e1',
  tourism_bureau: '#1a73e8',
  government: '#c0392b',
}

const businesses = ref([])
const providers = ref([])
const businessId = ref(1)
const plans = ref([])
const error = ref('')
const creating = ref(false)
const loaded = ref(false)
const copiedId = ref(null)
const businessPlatforms = ref([])
const planInfo = ref(null)
const previewingId = ref(null)

const plan = reactive({
  name: '', business_keywords: '', activity_name: '', promotional_points: '',
  event_time: '', event_location: '', event_description: '',
  provider: 'deepseek', channels: [],
})

const configuredProviders = computed(() => providers.value.filter(p => p.configured))
const allowedChannels = computed(() => {
  if (!businessPlatforms.value.length) return CHANNELS
  const filtered = {}
  for (const key of businessPlatforms.value) {
    if (CHANNELS[key]) filtered[key] = CHANNELS[key]
  }
  return filtered
})

function planStatusLabel(s) { return { draft: '草稿', in_progress: '进行中', published: '已发布' }[s] || s }
function planStatusClass(s) { return { draft: 'draft', in_progress: 'active', published: 'published' }[s] || '' }
function assetStatusLabel(s) { return { draft: '待审核', reviewed: '已审核', published: '已发布' }[s] || s }
function assetStatusClass(s) { return { draft: 'draft', reviewed: 'active', published: 'published' }[s] || '' }
function campaignSummary(input) { return Object.values(input || {}).filter(v => v).join(' · ') }
function themeColor(channel) { return CHANNEL_THEMES[channel] || '#116b55' }
function renderMarkdown(text) { return marked.parse(text || '') }
function togglePreview(id) { previewingId.value = previewingId.value === id ? null : id }

async function copyAsset(asset) {
  const text = `${asset.title}\n\n${asset.content}`
  try {
    await navigator.clipboard.writeText(text)
    copiedId.value = asset.id
    setTimeout(() => { if (copiedId.value === asset.id) copiedId.value = null }, 2000)
  } catch (e) { error.value = '复制失败，请手动复制' }
}

async function loadPlans() {
  try {
    const { data } = await api.get('/feeding-plans', { params: { business_id: businessId.value } })
    const allowed = businessPlatforms.value
    plans.value = data.map(p => ({
      ...p,
      assets: p.assets
        .filter(a => allowed.length === 0 || allowed.includes(a.channel))
        .map(a => ({ ...a, publishUrl: a.published_url || '' })),
    }))
    loaded.value = true
  } catch (e) { error.value = e.response?.data?.detail || e.message }
}

async function loadBusinessPlatforms() {
  try {
    const { data } = await api.get(`/businesses/${businessId.value}/platforms`)
    planInfo.value = data
    businessPlatforms.value = data.platforms.map(p => p.key)
    plan.channels = [...businessPlatforms.value]
  } catch (e) { businessPlatforms.value = [] }
}

async function onBusinessChange() {
  await loadBusinessPlatforms()
  await loadPlans()
}

async function createPlan() {
  if (!plan.name.trim()) { error.value = '请填写计划名称'; return }
  if (!plan.channels.length) { error.value = '请至少选择一个平台'; return }
  const hasContent = plan.business_keywords || plan.activity_name || plan.promotional_points ||
    plan.event_time || plan.event_location || plan.event_description
  if (!hasContent) { error.value = '请至少填写关键词、活动、宣传点或事件中的一项'; return }
  creating.value = true
  error.value = ''
  try {
    await api.post('/feeding-plans', {
      business_id: businessId.value, name: plan.name, provider: plan.provider, channels: plan.channels,
      business_keywords: plan.business_keywords, activity_name: plan.activity_name,
      promotional_points: plan.promotional_points, event_time: plan.event_time,
      event_location: plan.event_location, event_description: plan.event_description,
    })
    await loadPlans()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
  finally { creating.value = false }
}

async function saveAsset(asset) {
  try {
    await api.put(`/feeding-assets/${asset.id}`, { title: asset.title, content: asset.content })
    await loadPlans()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
}

async function uploadImage(asset, event) {
  const file = event.target.files[0]
  if (!file) return
  const formData = new FormData()
  formData.append('file', file)
  formData.append('alt_text', '')
  try {
    await api.post(`/feeding-assets/${asset.id}/images`, formData)
    await loadPlans()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
}

async function deleteImage(asset, imageId) {
  try {
    await api.delete(`/feeding-assets/${asset.id}/images/${imageId}`)
    await loadPlans()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
}

async function reviewAsset(asset) {
  try {
    await api.post(`/feeding-assets/${asset.id}/review`)
    await loadPlans()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
}

async function publishAsset(asset) {
  if (!asset.publishUrl.trim()) { error.value = '请填写发布链接'; return }
  try {
    await api.put(`/feeding-assets/${asset.id}/publish`, { published_url: asset.publishUrl })
    await loadPlans()
  } catch (e) { error.value = e.response?.data?.detail || e.message }
}

onMounted(async () => {
  try {
    businesses.value = await getBusinesses()
    providers.value = await getProviders()
    if (businesses.value.length) businessId.value = businesses.value[0].id
    if (configuredProviders.value.length) plan.provider = configuredProviders.value[0].id
    await loadBusinessPlatforms()
    await loadPlans()
  } catch (e) { error.value = e.message }
})
</script>

<style scoped>
.card { background: #fff; border: 1px solid #e4ebe6; border-radius: 14px; padding: 22px 24px; margin-bottom: 20px; }
.card h2 { margin: 0 0 16px; font-size: 17px; }
.form-grid { display: grid; gap: 12px; }
.form-row { display: flex; align-items: flex-start; gap: 12px; }
.form-row label { font-size: 14px; color: #70817b; min-width: 80px; padding-top: 8px; }
.input, textarea { padding: 8px 12px; border: 1px solid #e4ebe6; border-radius: 8px; font-size: 14px; background: #fff; flex: 1; font-family: inherit; }
textarea { width: 100%; }
.btn { padding: 9px 16px; border: 1px solid #e4ebe6; border-radius: 9px; background: #fff; cursor: pointer; font-size: 14px; text-decoration: none; color: #182d2a; display: inline-block; }
.btn.primary { background: #116b55; color: #fff; border-color: #116b55; }
.btn.small { padding: 5px 10px; font-size: 13px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.tag { display: inline-block; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.tag.draft { background: #fff7e6; color: #b45309; }
.tag.active { background: #e9f5ef; color: #116b55; }
.tag.published { background: #eef2ff; color: #4f46e5; }
.check-group { display: flex; gap: 14px; flex-wrap: wrap; }
.check { display: flex; align-items: center; gap: 6px; font-size: 14px; }
.error { color: #b91c1c; font-size: 13px; margin-top: 10px; }
.plan-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.asset { border: 1px solid #e4ebe6; border-radius: 12px; padding: 16px; margin-top: 16px; }
.asset-header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.asset-title { font-size: 15px; font-weight: 600; margin: 0 0 8px; }
.asset-content { white-space: pre-wrap; font-size: 14px; line-height: 1.7; color: #33403c; }
.images { display: flex; gap: 12px; flex-wrap: wrap; margin: 14px 0; }
.image-item { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.thumb { width: 140px; height: 100px; object-fit: cover; border-radius: 8px; border: 1px solid #e4ebe6; }
.upload { position: relative; cursor: pointer; margin: 4px 0 14px; }
.asset-actions { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.asset-actions .input { max-width: 340px; }
.guide-bar { display: flex; align-items: center; gap: 10px; margin-top: 14px; padding-top: 14px; border-top: 1px dashed #e4ebe6; }
.publish-preview { border: 1px solid #e4ebe6; border-radius: 12px; overflow: hidden; margin-top: 16px; background: #fff; }
.preview-bar { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; color: #fff; font-weight: 600; font-size: 14px; }
.preview-badge { font-size: 11px; font-weight: 400; background: rgba(255,255,255,.22); padding: 2px 10px; border-radius: 999px; }
.preview-body { padding: 20px 22px; }
.preview-title { font-size: 22px; font-weight: 700; margin: 0 0 14px; line-height: 1.4; }
.preview-images { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.preview-img { width: 200px; height: 140px; object-fit: cover; border-radius: 10px; border: 1px solid #e4ebe6; }
.preview-content :deep(h2) { font-size: 17px; margin: 18px 0 8px; }
.preview-content :deep(h3) { font-size: 15px; margin: 14px 0 6px; }
.preview-content :deep(p) { margin: 8px 0; line-height: 1.7; }
.preview-content :deep(ul), .preview-content :deep(ol) { padding-left: 22px; margin: 8px 0; }
.preview-content :deep(li) { margin: 4px 0; }
.preview-content :deep(strong) { font-weight: 700; }
.preview-content { font-size: 14.5px; color: #26332e; line-height: 1.7; }
.hint { color: #94a3b8; font-size: 13px; padding: 9px 0; display: inline-block; }

</style>
