/**
 * 会话/令牌管理。
 *
 * D49 的核心: 401 恢复路径。这里的 refresh 只能用**独立 axios 实例**，
 * 不能复用 api/request.ts 的实例 —— 否则 refresh 自身返回 401 时会再次进入
 * 同一个响应拦截器，形成 401 → refresh → 401 的递归。
 *
 * 并发去重: 同一时刻多个 401 共用同一个 refreshPromise(单例 Promise)，
 * 只有一次 /auth/refresh 真实请求，其余等待同一个 Promise 结果。
 *
 * 刷新后补齐用户信息: 后端 /auth/refresh 只返回 { access_token, refresh_token }
 * (不含 user / 不含 must_change_password)，所以刷新成功后主动拉一次
 * GET /auth/profile(该接口返回 _profile_payload，含 must_change_password)，
 * 把最新的 user_info 写回 localStorage 并同步给内存 store。
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'
import { ElMessage } from 'element-plus'
import {
  ACCESS_TOKEN_KEY,
  REFRESH_TOKEN_KEY,
  USER_INFO_KEY,
  clearAuthStorage,
  getAccessToken,
  getRefreshToken,
} from '@/utils/token'

export const API_BASE = '/api/v1'
export const SESSION_EXPIRED_MESSAGE = '登录已过期，请重新登录'

interface TokenPair {
  access_token?: string
  refresh_token?: string
}

interface RefreshEnvelope {
  code?: number
  message?: string
  data?: TokenPair
}

interface ProfileEnvelope {
  code?: number
  message?: string
  data?: Record<string, unknown>
}

/** 独立的 refresh 客户端: 不挂任何业务拦截器，避免递归与提示叠加 */
const refreshClient: AxiosInstance = axios.create({ baseURL: API_BASE, timeout: 15000 })

/** 单例 Promise: 并发 401 只触发一次 refresh */
let refreshPromise: Promise<string> | null = null
/** 「登录已过期」只提示一次(多次 401 不刷屏) */
let sessionExpiredNotified = false
/** 会话失效的整体流程(清存储 + 跳转)只执行一次，避免并发 401 触发多次跳转 */
let sessionDropped = false
/** 用户信息同步回调(由 stores/user.ts 注册，避免 api 层反向依赖 pinia) */
let profileSyncHandler: ((user: Record<string, unknown>) => void) | null = null

export function hasRefreshToken(): boolean {
  return Boolean(getRefreshToken())
}

export function resetSessionExpiredFlag(): void {
  sessionExpiredNotified = false
  sessionDropped = false
}

/** 注册(或清除)用户信息同步回调；store 里用它在 refresh 后更新内存态 */
export function registerProfileSync(handler: ((user: Record<string, unknown>) => void) | null): void {
  profileSyncHandler = handler
}

/**
 * 用当前 access_token 重新拉取 /auth/profile 并写回存储。
 * 失败不抛出: profile 只是锦上添花，不能因此让 refresh 流程失败。
 */
export async function syncUserProfile(): Promise<Record<string, unknown> | null> {
  const accessToken = getAccessToken()
  if (!accessToken) return null
  try {
    const response = await refreshClient.get('/auth/profile', {
      headers: { Authorization: 'Bearer ' + accessToken },
    })
    const body = response?.data as ProfileEnvelope | Record<string, unknown> | undefined
    const candidate = (body as ProfileEnvelope)?.data ?? (body as Record<string, unknown> | undefined)
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return null
    const user = candidate as Record<string, unknown>
    localStorage.setItem(USER_INFO_KEY, JSON.stringify(user))
    profileSyncHandler?.(user)
    return user
  } catch {
    return null
  }
}

async function requestNewToken(): Promise<string> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) throw new Error('缺少 refresh_token')
  const response = await refreshClient.post('/auth/refresh', { refresh_token: refreshToken })
  const body = response?.data as RefreshEnvelope | TokenPair | undefined
  // 后端 refresh 只返回 { access_token, refresh_token }(不含 user)
  const payload: TokenPair = (body as RefreshEnvelope)?.data ?? (body as TokenPair) ?? {}
  const accessToken = payload?.access_token
  if (!accessToken) throw new Error('刷新响应缺少 access_token')
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  if (payload.refresh_token) localStorage.setItem(REFRESH_TOKEN_KEY, payload.refresh_token)
  sessionExpiredNotified = false
  // refresh 不返回 user: 主动补一次 profile，保证 must_change_password / role 不滞后
  await syncUserProfile()
  return accessToken
}

/** 拿(或复用)一次刷新；失败时 Promise 一定是 rejected */
export function refreshAccessToken(): Promise<string> {
  if (!refreshPromise) {
    refreshPromise = requestNewToken().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

/** 清空本地会话 + 只提示一次 */
export function dropSession(): void {
  if (sessionDropped) return
  sessionDropped = true
  clearAuthStorage()
  if (!sessionExpiredNotified) {
    sessionExpiredNotified = true
    ElMessage.error(SESSION_EXPIRED_MESSAGE)
  }
}

/** 清会话并跳登录(带 redirect 回跳) */
export function handleSessionExpired(): void {
  if (sessionDropped) return
  dropSession()
  if (typeof window === 'undefined') return
  const { pathname, search } = window.location
  if (pathname === '/login') return
  const redirect = encodeURIComponent(pathname + search)
  window.location.replace('/login?redirect=' + redirect)
}
