<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand"><span class="logo">◈</span><span>GEO 智能监测</span></div>
      <nav class="nav">
        <router-link v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.title }}
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <div class="coverage-line">
          引擎覆盖 <b>{{ coverage.implemented || 0 }}/{{ coverage.total || 7 }}</b> 已接线 ·
          <b>{{ coverage.configured || 0 }}</b> 可用
        </div>
        <div v-for="p in wiredEngines" :key="p.engine" class="model-row" :title="p.wire_hint || ''">
          <span :class="['dot', dotClass(p)]"></span>
          <span>{{ p.label }}</span>
          <span class="muted">{{ stateLabel(p) }}</span>
        </div>
        <div class="model-row" title="通义千问 / 文心一言 / 智谱清言 / 腾讯元宝：适配器未编写，接线要点见可信度量台">
          <span class="dot off"></span>
          <span>其余 {{ unwiredEngines.length }} 家</span>
          <span class="muted">未接线</span>
        </div>
        <router-link to="/v2/console" class="footer-link">查看接线要点 →</router-link>
      </div>
    </aside>
    <main class="content"><router-view /></main>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { getEngineCatalog } from './api'

const navItems = [
  { path: '/v2/console', title: '可信度量台' },
  { path: '/v2/channel', title: '渠道分发' },
  { path: '/situation', title: '态势展示' },
  { path: '/members', title: '会员管理' },
  { path: '/overview', title: '效果总览' },
  { path: '/citation-insight', title: '引用洞察' },
  { path: '/questions', title: '问题工厂' },
  { path: '/feeding', title: '投喂中心' },
  { path: '/reports', title: '检测报告' },
]


const engines = ref([])
const coverage = ref({})

const wiredEngines = computed(() => engines.value.filter((e) => e.implemented))
const unwiredEngines = computed(() => engines.value.filter((e) => !e.implemented))

/** 三档状态，别把「有代码但没 Key」和「压根没接线」混成一个灰点。 */
function dotClass(e) {
  if (!e.implemented) return 'off'
  return e.configured ? 'on' : 'warn'
}
function stateLabel(e) {
  if (!e.implemented) return '未接线'
  if (!e.configured) return '缺 Key'
  return e.memory_mode ? '记忆口径' : '可跑'
}

onMounted(async () => {
  try {
    const cat = await getEngineCatalog()
    engines.value = cat.engines || []
    coverage.value = cat.coverage || {}
  } catch (e) {
    console.error(e)
  }
})
</script>
