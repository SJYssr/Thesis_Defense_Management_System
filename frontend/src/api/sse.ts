/**
 * 流式(SSE)请求的鉴权封装。
 *
 * 为什么需要它: AiChat 的 /ai/chat/stream 必须用原生 fetch 读 ReadableStream，
 * 走不了 axios 拦截器，于是拿不到 D49 的 401 自动恢复。这里把同一套策略
 * (先看 exp、过期先 refresh、401 视为会话失效)抽成独立模块，
 * 既给 fetch 复用，也能被脚本直接 import 做验证。
 */
import { SESSION_EXPIRED_MESSAGE, handleSessionExpired, hasRefreshToken, refreshAccessToken } from './session'
import { getAccessToken, isTokenExpired } from '@/utils/token'

/**
 * 取一个可用于发起流式请求的 access_token。
 * - 未过期: 直接用
 * - 已过期/无 token: 有 refresh_token 就先刷新，刷新失败或没有 refresh_token -> 按会话失效处理
 * 返回 null 表示会话已失效(调用方直接放弃本次请求，跳转/提示已由 session 完成)。
 */
export async function ensureStreamToken(): Promise<string | null> {
  const current = getAccessToken()
  if (current && !isTokenExpired(current)) return current
  if (!hasRefreshToken()) {
    handleSessionExpired()
    return null
  }
  let refreshed: string | null = null
  try {
    refreshed = await refreshAccessToken()
  } catch {
    handleSessionExpired()
    return null
  }
  if (!refreshed || isTokenExpired(refreshed)) {
    handleSessionExpired()
    return null
  }
  return refreshed
}

/**
 * 带鉴权与 401 恢复的 fetch。
 * 返回 Response(可能是 4xx，由调用方判断)；会话失效时抛 SESSION_EXPIRED_MESSAGE。
 */
export async function authorizedFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = await ensureStreamToken()
  if (!token) throw new Error(SESSION_EXPIRED_MESSAGE)
  const headers = new Headers(init.headers)
  headers.set('Authorization', 'Bearer ' + token)
  const response = await fetch(input, { ...init, headers })
  if (response.status === 401) {
    // 流式接口拿不到 axios 拦截器，这里等价于响应拦截器的 401 分支
    handleSessionExpired()
    throw new Error(SESSION_EXPIRED_MESSAGE)
  }
  return response
}

export { SESSION_EXPIRED_MESSAGE }
