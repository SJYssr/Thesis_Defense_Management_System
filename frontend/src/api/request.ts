import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
})

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

request.interceptors.response.use(
  (response) => {
    const res = response.data
    if (res && typeof res.code === 'number' && res.code !== 0) {
      // 统一业务错误提示(42901 限流静默交给调用方提示逻辑)
      if (res.code !== 42901) {
        ElMessage.error(res.message || '请求失败')
      }
      return Promise.reject(new Error(res.message || `code=${res.code}`))
    }
    return res
  },
  async (error) => {
    const msg = error.response?.data?.message || error.message || '网络错误'
    ElMessage.error(msg)
    return Promise.reject(error)
  },
)

export default request
