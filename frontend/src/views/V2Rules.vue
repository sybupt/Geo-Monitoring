<template>
  <div class="page">
    <div class="hero">
      <div class="eyebrow">P1 · 规则 → 内容 → 投喂 → 复测</div>
      <h1>规则学习与投喂闭环</h1>
      <p>
        按「引擎 × 渠道」学规则，再按规则生成对应渠道的稿子；生成后先过事实校验（数字必须有出处、不得有极限词），
        通过才允许投喂。当前 Key 已停用，复测走 mock 模拟引擎。
      </p>
    </div>

    <div class="banner warn">
      <strong>口径：</strong>
      复测数字来自 mock 模拟引擎（按稿件校验得分换算引用概率），只能说明链路打通与规则生效逻辑，
      <u>不能作为对外的效果证据</u>。接上真实 Key 后，用「实测学习」重训规则，再用真实跑批复测。
    </div>

    <!-- 规则库 -->
    <section class="section">
      <h2>
        规则库
        <span class="muted">{{ rules.total }} 条 · 覆盖 {{ rules.by_dimension.length }} 个维度</span>
      </h2>
      <div class="row">
        <label class="field">
          引擎
          <select v-model="ruleForm.engine">
            <option v-for="e in engines" :key="e.engine" :value="e.engine">{{ e.label }}</option>
          </select>
        </label>
        <button class="btn ghost" :disabled="busy.learn" @click="doLearn">
          {{ busy.learn ? '学习中…' : '学一轮先验规则（全部引擎）' }}
        </button>
        <span class="tip">先验规则来自公开的引擎信源画像，状态标「待验证」；实测规则才会显示 lift</span>
      </div>

      <div class="dim-grid">
        <div v-for="d in dimsForEngine" :key="d.dimension" class="dim-card">
          <div class="dim-head">
            <span class="dim-name">{{ d.label || d.dimension }}</span>
            <span class="pill" :class="d.source === 'measured' ? 'ok' : 'gray'">{{ d.confidence }}</span>
          </div>
          <div class="dim-meta">权重 {{ d.weight }} · {{ d.channel ? d.channel : '不限渠道' }}</div>
          <div class="dim-inst">{{ d.instruction }}</div>
          <div v-if="d.source === 'measured'" class="dim-meta">证据 {{ d.evidence_n }} 条 · lift {{ d.lift }}</div>
        </div>
        <div v-if="!dimsForEngine.length" class="muted">该引擎还没有规则，点上面的按钮冷启动一轮。</div>
      </div>
    </section>

    <!-- 生成 -->
    <section class="section">
      <h2>按规则生成投喂稿</h2>
      <div class="row">
        <label class="field">
          企业
          <select v-model="genForm.business_id">
            <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}（{{ b.category }}）</option>
          </select>
        </label>
        <label class="field">
          目标引擎
          <select v-model="genForm.engine">
            <option v-for="e in engines" :key="e.engine" :value="e.engine">{{ e.label }}</option>
          </select>
        </label>
        <label class="field">
          目标渠道
          <select v-model="genForm.channel">
            <option v-for="c in channels" :key="c.key" :value="c.key">{{ c.label }}</option>
          </select>
        </label>
        <button class="btn" :disabled="busy.generate" @click="doGenerate">
          {{ busy.generate ? '生成中…' : '生成并校验' }}
        </button>
      </div>
      <div v-if="genErr" class="err">{{ genErr }}</div>

      <div v-if="generated" class="gen-result">
        <div class="gen-head">
          <div>
            <div class="gen-title">{{ generated.title }}</div>
            <div class="dim-meta">
              {{ generated.channel_label }} · {{ generated.channel_genre }} · {{ generated.chars }} 字 ·
              命中 {{ hitCount }}/{{ generated.rule_hits.length }} 条规则
            </div>
          </div>
          <span class="pill" :class="generated.validation.pass ? 'ok' : 'bad'">
            {{ generated.validation.pass ? '校验通过' : '校验未通过' }}
          </span>
        </div>

        <div v-if="generated.validation.issues.length" class="issues">
          <div v-for="(i, idx) in generated.validation.issues" :key="idx" :class="['issue', i.level]">
            <strong>{{ i.level === 'fatal' ? '阻塞' : '提示' }}</strong>：{{ i.message }}
            <div class="tip">{{ i.detail }}</div>
          </div>
        </div>

        <div class="columns">
          <pre class="content-box">{{ generated.content }}</pre>
          <div class="side">
            <div class="sub-title">规则命中</div>
            <div v-for="h in generated.rule_hits" :key="h.dimension" class="hit-row">
              <span :class="['dot', h.applied ? 'on' : 'off']"></span>
              <span class="hit-name">{{ h.label }}</span>
              <span class="hit-how">{{ h.applied ? h.how : h.reason }}</span>
            </div>
            <div v-if="generated.facts_missing?.length" class="banner warn" style="margin-top:12px">
              企业事实缺：{{ generated.facts_missing.join('、') }} —— 不补的话部分规则无法满足
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 一键闭环 -->
    <section class="section">
      <h2>一键跑闭环（投喂前 → 出稿 → 投喂 → 复测）</h2>
      <div class="row">
        <label class="field">
          企业
          <select v-model="loopForm.business_id">
            <option v-for="b in businesses" :key="b.id" :value="b.id">{{ b.name }}</option>
          </select>
        </label>
        <label class="field">
          每轮采样
          <select v-model.number="loopForm.n_samples">
            <option :value="2">2</option>
            <option :value="3">3</option>
            <option :value="5">5</option>
          </select>
        </label>
        <button class="btn" :disabled="busy.loop" @click="doLoop">
          {{ busy.loop ? '跑批中，约 10~30 秒…' : '跑一轮闭环' }}
        </button>
        <button class="btn ghost" :disabled="busy.loop" @click="doClear">清空模拟语料</button>
      </div>
      <div v-if="loopErr" class="err">{{ loopErr }}</div>

      <div v-if="loopResult" class="loop-result">
        <div class="loop-cards">
          <div class="loop-card">
            <div class="loop-label">投喂前</div>
            <div class="loop-value">{{ pct(loopResult.before.mention_rate) }}</div>
            <div class="metric-ci">95% 区间 {{ ci(loopResult.before.mention_ci) }} · {{ loopResult.before.samples }} 次采样</div>
          </div>
          <div class="loop-arrow">→</div>
          <div class="loop-card">
            <div class="loop-label">投喂后</div>
            <div class="loop-value">{{ pct(loopResult.after.mention_rate) }}</div>
            <div class="metric-ci">95% 区间 {{ ci(loopResult.after.mention_ci) }} · {{ loopResult.after.samples }} 次采样</div>
          </div>
          <div class="loop-card">
            <div class="loop-label">变化</div>
            <div class="loop-value" :class="loopResult.delta > 0 ? 'up' : 'down'">
              {{ loopResult.delta > 0 ? '+' : '' }}{{ (loopResult.delta * 100).toFixed(1) }} pp
            </div>
            <div class="metric-ci">首位提及 {{ pct(loopResult.before.first_mention_rate) }} → {{ pct(loopResult.after.first_mention_rate) }}</div>
          </div>
        </div>

        <table class="table">
          <thead>
            <tr><th>渠道</th><th>标题</th><th>字数</th><th>规则得分</th><th>校验</th><th>投喂</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in loopResult.contents" :key="c.content_id">
              <td>{{ c.channel_label }}</td>
              <td>{{ c.title }}</td>
              <td>{{ c.chars }}</td>
              <td>{{ c.score.toFixed(2) }}</td>
              <td><span class="pill" :class="c.pass ? 'ok' : 'bad'">{{ c.pass ? '通过' : '未通过' }}</span></td>
              <td>{{ c.fed ? '已投喂' : '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="tip" style="margin-top:10px">{{ loopResult.notice }}</div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  clearSimCorpus, generateContent, getBusinesses, getEngineCatalog, getRules,
  learnRules, runLoopDemo,
} from '../api'

const channels = [
  { key: 'zhihu', label: '知乎' },
  { key: 'baijiahao', label: '百家号' },
  { key: 'baike', label: '百科条目' },
  { key: 'xiaohongshu', label: '小红书' },
  { key: 'douyin', label: '抖音' },
  { key: 'dianping', label: '大众点评' },
  { key: 'meituan', label: '美团' },
  { key: 'wechat', label: '微信公众号' },
  { key: 'amap', label: '高德地图' },
  { key: 'gov_news', label: '政务/官媒' },
]

const engines = ref([])
const businesses = ref([])
const rules = ref({ total: 0, by_dimension: [], rules: [] })
const generated = ref(null)
const loopResult = ref(null)
const genErr = ref('')
const loopErr = ref('')
const busy = reactive({ learn: false, generate: false, loop: false })

const ruleForm = reactive({ engine: 'mock' })
const genForm = reactive({ business_id: null, engine: 'mock', channel: 'zhihu' })
const loopForm = reactive({ business_id: null, n_samples: 3 })

const dimsForEngine = computed(() => {
  const seen = new Set()
  return (rules.value.rules || []).filter((r) => {
    if (r.engine !== ruleForm.engine) return false
    const k = r.dimension + r.channel
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
})
const hitCount = computed(() => (generated.value?.rule_hits || []).filter((h) => h.applied).length)

const pct = (v) => (v == null ? '—' : (v * 100).toFixed(1) + '%')
const ci = (v) => (Array.isArray(v) ? `${(v[0] * 100).toFixed(0)}%~${(v[1] * 100).toFixed(0)}%` : '—')

async function loadRules() {
  rules.value = await getRules({ engine: ruleForm.engine })
}

async function doLearn() {
  busy.learn = true
  try {
    const payload = { engines: engines.value.map((e) => e.engine), channels: channels.map((c) => c.key) }
    await learnRules(payload)
    await loadRules()
  } finally {
    busy.learn = false
  }
}

async function doGenerate() {
  busy.generate = true
  genErr.value = ''
  try {
    generated.value = await generateContent({
      business_id: genForm.business_id,
      engine: genForm.engine,
      channel: genForm.channel,
    })
  } catch (e) {
    genErr.value = e?.response?.data?.detail || '生成失败'
  } finally {
    busy.generate = false
  }
}

async function doLoop() {
  busy.loop = true
  loopErr.value = ''
  try {
    loopResult.value = await runLoopDemo({
      business_id: loopForm.business_id,
      engines: [genForm.engine],
      n_samples: loopForm.n_samples,
      reset: true,
    })
  } catch (e) {
    loopErr.value = e?.response?.data?.detail || '闭环跑批失败'
  } finally {
    busy.loop = false
  }
}

async function doClear() {
  await clearSimCorpus(loopForm.business_id)
  loopResult.value = null
}

onMounted(async () => {
  const [cat, biz] = await Promise.all([getEngineCatalog(), getBusinesses()])
  // 引擎目录只含 7 个真实引擎；规则/生成/闭环都支持 mock，手动补一个选项
  engines.value = [...cat.engines, { engine: 'mock', label: 'Mock（演练·免费）' }]
  businesses.value = biz
  ruleForm.engine = 'mock'
  genForm.business_id = biz[0]?.id ?? null
  loopForm.business_id = biz[0]?.id ?? null
  await loadRules()
})
</script>

<style scoped>
.row { display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap; margin-bottom: 16px; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); min-width: 160px; }
.field select { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; background: #fff; color: var(--ink); }
.btn { padding: 9px 16px; border-radius: 8px; border: 1px solid var(--green); background: var(--green); color: #fff; font-size: 14px; cursor: pointer; }
.btn.ghost { background: #fff; color: var(--green); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.tip { font-size: 11px; color: #9db0a8; line-height: 1.6; }
.err { color: #b91c1c; font-size: 13px; margin: 8px 0; }
.banner { background: #fff; border: 1px solid var(--line); border-left: 3px solid var(--green); border-radius: 10px; padding: 12px 16px; font-size: 13px; color: var(--muted); line-height: 1.8; }
.banner.warn { border-left-color: #f59e0b; background: #fffbeb; color: #92400e; }
.dim-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.dim-card { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }
.dim-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.dim-name { font-weight: 600; font-size: 14px; }
.dim-meta { font-size: 11px; color: var(--muted); line-height: 1.7; }
.dim-inst { font-size: 12px; color: #4b5b54; line-height: 1.7; margin-top: 4px; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 20px; background: #eef2ef; color: var(--muted); }
.pill.ok { background: var(--mint); color: var(--green); }
.pill.bad { background: #fee2e2; color: #991b1b; }
.pill.gray { background: #f1f5f3; color: var(--muted); }
.gen-result, .loop-result { margin-top: 18px; background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; }
.gen-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 12px; }
.gen-title { font-size: 16px; font-weight: 600; }
.issues { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
.issue { font-size: 12px; padding: 8px 12px; border-radius: 8px; background: #fffbeb; color: #92400e; }
.issue.fatal { background: #fef2f2; color: #991b1b; }
.columns { display: grid; grid-template-columns: 1.35fr 1fr; gap: 22px; }
.content-box { white-space: pre-wrap; font-family: inherit; font-size: 13px; line-height: 1.9; color: var(--ink); background: #fbfcfb; border: 1px solid var(--line); border-radius: 10px; padding: 16px; max-height: 520px; overflow: auto; margin: 0; }
.sub-title { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
.hit-row { display: grid; grid-template-columns: 12px 88px 1fr; align-items: start; gap: 8px; font-size: 12px; margin-bottom: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 4px; background: #cbd5d1; }
.dot.on { background: #10b981; }
.hit-name { color: var(--ink); }
.hit-how { color: var(--muted); line-height: 1.6; }
.loop-cards { display: grid; grid-template-columns: 1fr auto 1fr 1fr; gap: 14px; align-items: center; margin-bottom: 18px; }
.loop-card { background: #fbfcfb; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
.loop-label { font-size: 12px; color: var(--muted); }
.loop-value { font-size: 26px; font-weight: 650; margin: 4px 0; }
.loop-value.up { color: #b42318; }
.loop-value.down { color: #0f766e; }
.loop-arrow { font-size: 20px; color: var(--muted); }
.metric-ci { font-size: 11px; color: var(--muted); line-height: 1.6; }
</style>
