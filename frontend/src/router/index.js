import { createRouter, createWebHistory } from 'vue-router'
import Overview from '../views/Overview.vue'

const routes = [
  { path: '/', redirect: '/v2/console' },
  { path: '/v2/console', name: 'v2-console', component: () => import('../views/V2Console.vue'), meta: { title: '可信度量台' } },
  { path: '/v2/channel', name: 'v2-channel', component: () => import('../views/V2Channel.vue'), meta: { title: '渠道分发' } },
  { path: '/situation', name: 'situation', component: () => import('../views/SituationView.vue'), meta: { title: '态势展示' } },
  { path: '/overview', name: 'overview', component: Overview, meta: { title: '效果总览' } },
  { path: '/citation-insight', name: 'citation-insight', component: () => import('../views/CitationInsight.vue'), meta: { title: '引用洞察' } },
  { path: '/questions', name: 'questions', component: () => import('../views/QuestionFactory.vue'), meta: { title: '问题工厂' } },
  { path: '/feeding', name: 'feeding', component: () => import('../views/FeedingCenter.vue'), meta: { title: '投喂中心' } },
  { path: '/reports', name: 'reports', component: () => import('../views/Reports.vue'), meta: { title: '检测报告' } },
  { path: '/members', name: 'members', component: () => import('../views/Members.vue'), meta: { title: '会员管理' } },
]

const router = createRouter({ history: createWebHistory(), routes })
export default router
