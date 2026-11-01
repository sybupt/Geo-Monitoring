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
        <div v-for="p in providers" :key="p.id" class="model-row">
          <span :class="['dot', p.configured ? 'on' : 'off']"></span>
          <span>{{ p.label }}</span>
          <span class="muted">{{ p.configured ? '已配置' : '未配置' }}</span>
        </div>
      </div>
    </aside>
    <main class="content"><router-view /></main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getProviders } from './api'

const navItems = [
  { path: '/situation', title: '态势展示' },
  { path: '/members', title: '会员管理' },
  { path: '/overview', title: '效果总览' },
  { path: '/citation-insight', title: '引用洞察' },
  { path: '/questions', title: '问题工厂' },
  { path: '/feeding', title: '投喂中心' },
  { path: '/reports', title: '检测报告' },
]


const providers = ref([])
onMounted(async () => {
  try { providers.value = await getProviders() } catch (e) { console.error(e) }
})
</script>
