import axios from 'axios'
import type { AxiosError, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { extractBackendMessage, NETWORK_ERROR_MESSAGE, resolveErrorMessage, SILENT_CODES } from '@/utils/error'
import { API_BASE, handleSessionExpired, refreshAccessToken } from './session'

interface RetriableConfig extends InternalAxiosRequestConfig {
  /** 该请求已经因 401 重放过一次，不再重试(D49) */
  _retried?: boolean
  /** 静默请求: 失败不弹全局提示，由调用方自行提示 */
  _silent?: boolean
}

interface ApiEnvelope {
  code?: number
  message?: string
  data?: unknown
}

const request = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
})

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = 'Bearer ' + token
  }
  return config
})

/** 这些端点自身负责 401 语义(密码错误/续期失败/登出)，绝不能再触发 refresh 重放 */
function isAuthEndpoint(url: string): boolean {
  return url.includes('/auth/login') || url.includes('/auth/refresh') || url.includes('/auth/logout')
}

function isSilentRequest(config?: RetriableConfig): boolean {
  if (config?._silent) return true
  return String(config?.url || '').includes('/auth/logout')
}

function notify(message: string, config?: RetriableConfig): void {
  if (!isSilentRequest(config)) ElMessage.error(message)
}

request.interceptors.response.use(
  (response) => {
    const config = response.config as RetriableConfig
    // 文件下载(blob)不解析统一响应体
    if (config.responseType === 'blob') {
      return response.data as unknown as AxiosResponse
    }
    const body = response.data as ApiEnvelope | undefined
    // 后端已改用真实 HTTP 状态码，这里只兜底极少数 code 非 0 但状态 200 的情况
    if (body && typeof body.code === 'number' && body.code !== 0) {
      const message = resolveErrorMessage(body.code, body.message, response.status)
      if (!SILENT_CODES.has(body.code)) notify(message, config)
      return Promise.reject(new Error(message)) as unknown as AxiosResponse
    }
    return body as unknown as AxiosResponse
  },
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined
    const response = error.response
    const status = response?.status ?? null
    const body = response?.data as ApiEnvelope | undefined
    const code = typeof body?.code === 'number' ? body.code : null
    const url = String(config?.url || '')

    // ===== D49: 401 恢复路径 =====
    if (status === 401 && !isAuthEndpoint(url)) {
      if (config && !config._retried) {
        config._retried = true
        try {
          // 并发去重: 多个 401 共用同一个 refresh Promise
          await refreshAccessToken()
          // 重放原请求一次(请求拦截器会带上新 token)
          return await request(config)
        } catch {
          handleSessionExpired()
          return Promise.reject(error)
        }
      }
      // 已经重放过仍然 401: 说明会话已彻底失效
      handleSessionExpired()
      return Promise.reject(error)
    }

    if (code !== null && SILENT_CODES.has(code)) {
      return Promise.reject(error)
    }

    const message =
      status === null
        ? NETWORK_ERROR_MESSAGE
        : resolveErrorMessage(code, extractBackendMessage(body), status)
    notify(message, config)
    return Promise.reject(error)
  },
)

export default request
