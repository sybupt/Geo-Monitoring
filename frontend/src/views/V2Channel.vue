<template>
  <div class="page">
    <div class="hero">
      <div class="eyebrow">P2 · 渠道占位</div>
      <h1>多源投放与收录回查</h1>
      <p>
        AI 只会引用它检索得到的内容。这里按渠道画像安排投放：能直发的走接口，需登录的给你发布页 + 一键复制，
        发完回填链接，再回查是否还活着。<b>发出去 ≠ 被收录 ≠ 被引擎引用</b>，三层分别记录，不混为一谈。
      </p>
    </div>

    <section class="section">
      <h2>新任务</h2>
      <div class="form-grid">
        <label>
          企业
          <select v-model.number="businessId">
            <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
          </select>
        </label>
        <label>
          渠道
          <select v-model="form.channel_key">
            <option v-for="c in channels" :key="c.key" :value="c.key">
              {{ c.label }}（{{ modeLabel(c.mode) }}）
            </option>
          </select>
        </label>
        <label class="wide">
          标题
          <input v-model="form.title" placeholder="清镇看牙去哪家？本地人真实体验" />
        </label>
        <label class="wide">
          正文
          <textarea v-model="form.content" rows="4" placeholder="按渠道体改写：小红书口语化、知乎长文、百家号深度稿…"></textarea>
        </label>
        <label class="wide">
          话题标签<span class="tip">空格或逗号分隔，自动加 #</span>
          <input v-model="form.topicsText" placeholder="清镇口腔 本地生活" />
        </label>
      </div>

      <div v-if="currentChannel" class="channel-hint">
        <div><b>{{ currentChannel.label }}</b> · AI 信源权重 {{ currentChannel.ai_weight }}</div>
        <div class="muted">{{ currentChannel.note }}</div>
        <div class="muted small">竞品占位检索：{{ currentChannel.probe_query }}</div>
      </div>

      <div class="actions">
        <button class="btn" @click="doCreate" :disabled="busy">生成发布任务</button>
        <span v-if="msg" class="ok-msg">{{ msg }}</span>
        <span v-if="errMsg" class="err">{{ errMsg }}</span>
      </div>
    </section>

    <section class="section">
      <h2>渠道账号</h2>
      <div class="actions" style="margin-top: 0">
        <select v-model="accForm.channel_key">
          <option v-for="c in channels" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
        <input v-model="accForm.account_name" placeholder="账号名（如：康美口腔官方号）" />
        <select v-model="accForm.bind_mode">
          <option value="guide">引导登录</option>
          <option value="rpa">RPA 代发</option>
          <option value="api">接口直发</option>
        </select>
        <button class="btn ghost" @click="doBind">登记账号</button>
      </div>
      <table class="table" style="margin-top: 12px">
        <thead><tr><th>渠道</th><th>账号</th><th>绑定方式</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="a in accounts" :key="a.id">
            <td>{{ a.channel_label }}</td>
            <td>{{ a.account_name || '—' }}</td>
            <td>{{ modeLabel(a.bind_mode) }}</td>
            <td><span class="pill ok">{{ a.status }}</span></td>
          </tr>
          <tr v-if="!accounts.length"><td colspan="4" class="muted">暂无绑定账号；只存凭据引用，不存明文密码</td></tr>
        </tbody>
      </table>
    </section>

    <section class="section">
      <h2>发布任务</h2>
      <table class="table">
        <thead>
          <tr><th>#</th><th>渠道</th><th>标题</th><th>方式</th><th>状态</th><th>收录</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>{{ t.id }}</td>
            <td>{{ t.channel_label }}</td>
            <td class="title-cell">{{ t.title || '（无标题）' }}</td>
            <td>{{ modeLabel(t.mode) }}</td>
            <td><span :class="['pill', t.status === 'published' ? 'ok' : 'warn']">{{ statusLabel(t.status) }}</span></td>
            <td>
              <span v-if="t.indexed" class="pill ok">链接存活</span>
              <a v-else-if="t.published_url" :href="t.published_url" target="_blank" class="link">待回查</a>
              <span v-else class="muted">—</span>
            </td>
            <td class="ops">
              <button class="btn tiny ghost" @click="copy(t)">复制文案</button>
              <button v-if="t.guide_url" class="btn tiny ghost" @click="openGuide(t)">去发布</button>
              <button v-if="!t.published_url" class="btn tiny ghost" @click="doMark(t)">回填链接</button>
              <button v-if="t.published_url" class="btn tiny ghost" @click="doCheck(t)">回查</button>
            </td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="7" class="muted">还没有发布任务</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  bindChannelAccount, checkPublished, createPublishTask, getBusinesses, getChannels,
  listChannelAccounts, listPublishTasks, markPublished,
} from '../api'

const businesses = ref([])
const channels = ref([])
const accounts = ref([])
const tasks = ref([])
const businessId = ref(null)
const busy = ref(false)
const msg = ref('')
const errMsg = ref('')

const form = reactive({ channel_key: 'xiaohongshu', title: '', content: '', topicsText: '' })
const accForm = reactive({ channel_key: 'dianping', account_name: '', bind_mode: 'guide' })

const currentChannel = computed(() => channels.value.find((c) => c.key === form.channel_key))
const splitList = (s) => s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
const modeLabel = (m) => ({ api: '接口直发', rpa: 'RPA 代发', guide: '引导发布' }[m] || m)
const statusLabel = (s) =>
  ({ awaiting_user: '待用户发布', ready: '待发送', published: '已发布', failed: '失败' }[s] || s)

async function loadAll() {
  if (!businessId.value) return
  const [acc, tk] = await Promise.all([listChannelAccounts(businessId.value), listPublishTasks(businessId.value)])
  accounts.value = acc
  tasks.value = tk
}

async function doCreate() {
  errMsg.value = ''
  msg.value = ''
  if (!form.title && !form.content) { errMsg.value = '标题和正文至少填一个'; return }
  busy.value = true
  try {
    await createPublishTask({
      business_id: businessId.value,
      channel_key: form.channel_key,
      title: form.title,
      content: form.content,
      topics: splitList(form.topicsText),
    })
    msg.value = '已生成，去「发布任务」里复制文案'
    form.title = ''
    form.content = ''
    form.topicsText = ''
    await loadAll()
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

async function doBind() {
  try {
    await bindChannelAccount({ business_id: businessId.value, ...accForm })
    accForm.account_name = ''
    await loadAll()
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || e.message
  }
}

function copy(t) {
  navigator.clipboard.writeText(t.copy_text || '').then(() => {
    msg.value = `任务 #${t.id} 文案已复制，去平台粘贴发布`
  })
}

function openGuide(t) {
  window.open(t.guide_url, '_blank')
  copy(t)
}

async function doMark(t) {
  const url = window.prompt('粘贴发布后的公开链接（这个链接要能被搜索引擎抓到）')
  if (!url) return
  await markPublished(t.id, url)
  await loadAll()
}

async function doCheck(t) {
  const res = await checkPublished(t.id)
  msg.value = res.alive ? `#${t.id} 页面可访问（HTTP ${res.status_code}）` : `#${t.id} ${res.note}`
  await loadAll()
}

watch(businessId, loadAll)

onMounted(async () => {
  ;[businesses.value, channels.value] = await Promise.all([
    getBusinesses(),
    getChannels().then((d) => d.channels),
  ])
  businessId.value = businesses.value[0]?.id ?? null
  await loadAll()
})
</script>

<style scoped>
.form-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); }
.form-grid .wide { grid-column: 1 / -1; }
.form-grid input, .form-grid select, .form-grid textarea,
.actions input, .actions select { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; background: #fff; color: var(--ink); font-family: inherit; }
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
.btn { padding: 9px 18px; border-radius: 8px; border: 1px solid var(--green); background: var(--green); color: #fff; font-size: 14px; cursor: pointer; }
.btn.ghost { background: #fff; color: var(--green); }
.btn.tiny { padding: 4px 10px; font-size: 12px; }
.btn:disabled { opacity: .5; }
.channel-hint { margin-top: 14px; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; font-size: 13px; line-height: 1.9; }
.tip { font-size: 11px; color: #9db0a8; }
.small { font-size: 12px; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 20px; background: #f1f5f3; color: var(--muted); }
.pill.ok { background: var(--mint); color: var(--green); }
.pill.warn { background: #fef3c7; color: #92400e; }
.title-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ops { display: flex; gap: 6px; flex-wrap: wrap; }
.link { color: var(--green); font-size: 12px; }
.err { color: #b91c1c; font-size: 13px; }
.ok-msg { color: var(--green); font-size: 13px; }
</style>
