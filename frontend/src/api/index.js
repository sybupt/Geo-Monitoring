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

export default api
