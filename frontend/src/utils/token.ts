/**
 * JWT / 会话存储相关的纯函数工具。
 * 注意: 只解析 payload 判断 exp，不做验签(前端无法也不该验签)，过期判断只为体验优化，
 * 真正的鉴权仍由后端 token_version + 401 兜底。
 */

export interface JwtPayload {
  sub?: string
  role?: string
  type?: string
  exp?: number
  iat?: number
  jti?: string
  token_version?: number
}

export const ACCESS_TOKEN_KEY = 'access_token'
export const REFRESH_TOKEN_KEY = 'refresh_token'
export const USER_INFO_KEY = 'user_info'

/** 提前 30s 视为过期，避免请求在途中跨过 exp */
export const TOKEN_EXPIRY_SKEW_SECONDS = 30

export const ROLE_HOME: Record<string, string> = {
  ADMIN: '/admin/home',
  TEACHER: '/teacher/home',
  STUDENT: '/student/home',
}

/** 解码 JWT payload(base64url, 无验签)，失败返回 null */
export function decodeJwtPayload(token?: string | null): JwtPayload | null {
  if (!token) return null
  const parts = token.split('.')
  if (parts.length < 2 || !parts[1]) return null
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
    const binary = atob(padded)
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as JwtPayload
    return payload && typeof payload === 'object' ? payload : null
  } catch {
    return null
  }
}

/** token 是否已过期；无 exp 或无法解析时返回 false(交给服务端 401 兜底) */
export function isTokenExpired(token?: string | null, skewSeconds = TOKEN_EXPIRY_SKEW_SECONDS): boolean {
  const exp = decodeJwtPayload(token)?.exp
  if (typeof exp !== 'number' || !Number.isFinite(exp)) return false
  return exp * 1000 <= Date.now() + skewSeconds * 1000
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function clearAuthStorage(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(USER_INFO_KEY)
}

export function readStoredUser<T = Record<string, unknown>>(): T | null {
  const raw = localStorage.getItem(USER_INFO_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

export function roleHome(role?: string | null): string {
  return (role && ROLE_HOME[role]) || '/login'
}
