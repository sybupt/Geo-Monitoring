import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 300000 })

export async function getBusinesses() {
  const { data } = await api.get('/businesses')
  return data
}

export async function getProviders() {
  const { data } = await api.get('/providers')
  return data
}

export async function getOverview(businessId) {
  const { data } = await api.get('/dashboard/overview', { params: { business_id: businessId } })
  return data
}

export async function getMonitorRuns(businessId, limit = 50) {
  const { data } = await api.get('/monitor-runs', { params: { business_id: businessId, limit } })
  return data
}

/* ── v2：多采样可信度量 / 归因 / 渠道占位 ─────────────────────── */

export async function getEngineCatalog() {
  const { data } = await api.get('/v2/engines')
  return data
}

export async function previewQuestions(payload) {
  const { data } = await api.post('/v2/questions/preview', payload)
  return data
}

export async function createBatch(payload) {
  const { data } = await api.post('/v2/batches', payload)
  return data
}

export async function listBatches(businessId) {
  const { data } = await api.get('/v2/batches', { params: { business_id: businessId } })
  return data
}

export async function getBatchReport(batchId) {
  const { data } = await api.get(`/v2/batches/${batchId}`)
  return data
}

export async function resumeBatch(batchId) {
  const { data } = await api.post(`/v2/batches/${batchId}/resume`)
  return data
}

export async function getDid(beforeId, afterId) {
  const { data } = await api.get('/v2/did', { params: { before: beforeId, after: afterId } })
  return data
}

export async function getJudgeQuality(batchId) {
  const { data } = await api.get('/v2/judge-quality', { params: { batch_id: batchId } })
  return data
}

export async function getChannels(city = '') {
  const { data } = await api.get('/v2/channels', { params: { city } })
  return data
}

export async function bindChannelAccount(payload) {
  const { data } = await api.post('/v2/channel-accounts', payload)
  return data
}

export async function listChannelAccounts(businessId) {
  const { data } = await api.get('/v2/channel-accounts', { params: { business_id: businessId } })
  return data
}

export async function createPublishTask(payload) {
  const { data } = await api.post('/v2/publish-tasks', payload)
  return data
}

export async function listPublishTasks(businessId) {
  const { data } = await api.get('/v2/publish-tasks', { params: { business_id: businessId } })
  return data
}

export async function markPublished(taskId, publishedUrl) {
  const { data } = await api.post(`/v2/publish-tasks/${taskId}/published`, { published_url: publishedUrl })
  return data
}

export async function checkPublished(taskId) {
  const { data } = await api.post(`/v2/publish-tasks/${taskId}/check`)
  return data
}

export default api
