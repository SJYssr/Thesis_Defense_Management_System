/**
 * 统一错误码 → 中文文案映射。
 *
 * 后端(backend/app/core/response.py)响应体结构恒为 { code, message, data }，
 * 且 P1-B 起真实使用 HTTP 状态码，映射关系见 EXPLICIT_HTTP_STATUS:
 *   40100→401 / 40300→403 / 40400→404 / 40900→409 / 42200→422 / 42901→429 / 50000,50001→500
 *
 * 提示优先级: 后端 message(通常更具体) > code 映射 > HTTP 状态映射 > 兜底文案。
 */

/** 业务码 → 文案(与后端 code 一一对应) */
export const ERROR_MESSAGE_MAP: Record<number, string> = {
  40002: '参数校验失败',
  /** 单文件超限(backend/app/core/exceptions.py:56) */
  40010: '单文件超出大小限制，请压缩后重试',
  /** 今日上传配额用尽(backend/app/modules/file/router.py:31) —— 与 40010 是不同原因, 不可合成一句 */
  40013: '今日上传配额已用尽，请明日再试或联系管理员调整上限',
  /** P2-A 统一状态机: 非法状态转移(场次/席位/结论) */
  40003: '当前状态不允许该操作，请刷新后重试',
  40100: '登录已过期，请重新登录',
  40300: '无权限访问该资源',
  40400: '资源不存在',
  40900: '数据冲突，请刷新后重试',
  42200: '参数校验失败',
  42901: '操作过于频繁，请稍后再试',
  50000: '服务异常，请稍后重试',
  50001: 'AI 服务异常，请稍后重试',
}

/** HTTP 状态 → 文案(响应体不是标准 envelope 时的兜底) */
export const HTTP_STATUS_MESSAGE: Record<number, string> = {
  400: '请求参数有误',
  401: '登录已过期，请重新登录',
  403: '无权限访问该资源',
  404: '资源不存在',
  409: '数据冲突，请刷新后重试',
  422: '参数校验失败',
  429: '操作过于频繁，请稍后再试',
  500: '服务异常，请稍后重试',
  502: '网关异常，请稍后重试',
  503: '服务暂时不可用，请稍后重试',
  504: '请求超时，请稍后重试',
}

export const NETWORK_ERROR_MESSAGE = '网络异常，请检查网络后重试'
export const DEFAULT_ERROR_MESSAGE = '请求失败，请稍后重试'

/**
 * 静默码: 不弹全局提示，由调用方按业务文案自行提示。
 * 42901(AI 限流) 现在走真实 HTTP 429，若每个并发请求都弹一次会刷屏，
 * 具体页面已有自己的提示，这里只负责静默。
 */
export const SILENT_CODES: ReadonlySet<number> = new Set<number>([42901])

/** 从后端响应体里取最具体的 message(兼容 FastAPI 的 detail 结构) */
export function extractBackendMessage(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return ''
  const body = payload as Record<string, unknown>
  if (typeof body.message === 'string' && body.message.trim()) return body.message.trim()
  const detail = body.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as Record<string, unknown> | undefined
    const msg = typeof first?.msg === 'string' ? first.msg : ''
    if (msg.trim()) return msg.trim()
  }
  return ''
}

/** 统一文案出口: 后端 message 优先，其次 code 映射，其次 HTTP 状态，最后兜底 */
export function resolveErrorMessage(
  code?: number | null,
  backendMessage?: string | null,
  httpStatus?: number | null,
): string {
  const message = (backendMessage || '').trim()
  if (message) return message
  if (code != null && ERROR_MESSAGE_MAP[code]) return ERROR_MESSAGE_MAP[code]
  if (httpStatus != null && HTTP_STATUS_MESSAGE[httpStatus]) return HTTP_STATUS_MESSAGE[httpStatus]
  return DEFAULT_ERROR_MESSAGE
}
