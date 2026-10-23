#!/usr/bin/env node
/**
 * D49 验证脚本: 401 恢复路径 + 并发去重。
 *
 * 做法(不 mock 被测逻辑): 用 module.register 解析钩子把 axios / element-plus 换成本地 stub，
 * 把 @/ 映射到真实 frontend/src，然后加载**真实的** src/api/request.ts 与 src/api/session.ts，
 * 通过注入 adapter 制造 401，观察拦截器行为。
 *
 * 覆盖:
 *   A. 并发 3 个 401 -> refresh 只调用 1 次 -> 3 个请求用新 token 重放成功
 *   B. refresh 失败 -> 清 localStorage + 跳 /login + 只提示一次「登录已过期」
 *   C. 重放后仍 401 -> 不再重试，按会话失效处理
 *   D. utils/error.ts 的文案优先级
 *   E. utils/token.ts 的 exp 判定(路由守卫依赖项)
 *
 * 零新增依赖: 依赖 Node 22+ 原生 TS 类型剥离能力。
 */
import path from 'node:path'
import module from 'node:module'
import { pathToFileURL, fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const SRC = path.join(FRONTEND, 'src')

// ---------------------------------------------------------------- 浏览器环境替身
class MemoryStorage {
  constructor() { this.map = new Map() }
  getItem(key) { return this.map.has(String(key)) ? this.map.get(String(key)) : null }
  setItem(key, value) { this.map.set(String(key), String(value)) }
  removeItem(key) { this.map.delete(String(key)) }
  clear() { this.map.clear() }
  get length() { return this.map.size }
}

const localStorageStub = new MemoryStorage()
const navigations = []
globalThis.localStorage = localStorageStub
globalThis.window = {
  location: {
    pathname: '/teacher/home',
    search: '?page=2',
    replace(url) { navigations.push(String(url)) },
  },
}

module.register(pathToFileURL(path.join(HERE, 'ts-resolve-hook.mjs')), import.meta.url)

const stubUrl = (name) => pathToFileURL(path.join(HERE, name)).href
const srcUrl = (rel) => pathToFileURL(path.join(SRC, rel)).href

const axiosStub = await import(stubUrl('axios-stub.mjs'))
const epStub = await import(stubUrl('element-plus-stub.mjs'))
const { default: request } = await import(srcUrl('api/request.ts'))
const session = await import(srcUrl('api/session.ts'))
const errorUtils = await import(srcUrl('utils/error.ts'))
const tokenUtils = await import(srcUrl('utils/token.ts'))
const sse = await import(srcUrl('api/sse.ts'))

// ---------------------------------------------------------------- 断言
const checks = []
function check(name, passed, detail) {
  checks.push({ name, passed })
  process.stdout.write((passed ? '[PASS] ' : '[FAIL] ') + name + '\n')
  if (detail) process.stdout.write('       ' + detail + '\n')
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const okBody = (data) => ({ status: 200, data: { code: 0, message: 'success', data } })
const fail401 = (message, code) => {
  const err = new Error('Request failed with status code 401')
  err.response = { status: 401, data: { code: code == null ? 40100 : code, message, data: null } }
  return err
}

function resetEnv(access, refresh) {
  localStorageStub.clear()
  if (access) localStorageStub.setItem('access_token', access)
  if (refresh) localStorageStub.setItem('refresh_token', refresh)
  localStorageStub.setItem('user_info', JSON.stringify({ id: 2, role: 'TEACHER', real_name: '张老师' }))
  axiosStub.resetStats()
  epStub.resetNotifyLog()
  navigations.length = 0
  session.resetSessionExpiredFlag()
}

process.stdout.write('===== D49 401 恢复路径验证 (真实 src/api/request.ts + session.ts) =====\n')

// ---------------------------------------------------------------- A: 并发去重
{
  resetEnv('expired-token', 'refresh-ok')
  const log = []
  axiosStub.setAdapter(async (cfg) => {
    const url = String(cfg.url || '')
    const auth = String((cfg.headers && cfg.headers.Authorization) || '')
    log.push({ url, auth })
    if (url === '/auth/refresh') {
      axiosStub.stats.refresh += 1
      await delay(40)
      return okBody({ access_token: 'fresh-token', refresh_token: 'refresh-2' })
    }
    if (url === '/auth/profile') return okBody({ id: 2, role: 'TEACHER', real_name: '张老师' })
    if (auth === 'Bearer expired-token') throw fail401('登录已过期')
    return okBody({ route: url })
  })
  const results = await Promise.all([
    request.get('/topics', { params: { page: 1 } }),
    request.get('/teacher/selections'),
    request.get('/notifications'),
  ])
  const business = log.filter((e) => e.url !== '/auth/refresh' && e.url !== '/auth/profile')
  const replays = business.filter((e) => e.auth === 'Bearer fresh-token')
  check(
    'A1 并发 3 个 401 只触发 1 次 refresh',
    axiosStub.stats.refresh === 1,
    '实测 /auth/refresh 调用次数 = ' + axiosStub.stats.refresh + ' (期望 1)',
  )
  check(
    'A2 3 个请求都重放成功且返回业务数据',
    results.length === 3 && results.every((r) => r && r.code === 0 && r.data && r.data.route),
    '实测返回: ' + results.map((r) => (r && r.data ? r.data.route : 'null')).join(', '),
  )
  check(
    'A3 业务请求共 6 次(3 次原始 401 + 3 次重放)',
    business.length === 6,
    '实测业务请求 ' + business.length + ' 次: ' + business.map((e) => e.url + '(' + (e.auth || 'no-token') + ')').join(' -> '),
  )
  check(
    'A4 重放携带刷新后的新 token',
    replays.length === 3 && replays.every((e) => e.auth === 'Bearer fresh-token'),
    '实测重放 Authorization: ' + replays.map((e) => e.auth).join(', '),
  )
  check('A5 恢复过程不弹错误提示', epStub.notifyLog.length === 0, '实测 ElMessage 调用 ' + epStub.notifyLog.length + ' 次')
  check(
    'A6 localStorage 的 token 已更新为新值',
    localStorageStub.getItem('access_token') === 'fresh-token' && localStorageStub.getItem('refresh_token') === 'refresh-2',
    'access_token=' + localStorageStub.getItem('access_token') + ', refresh_token=' + localStorageStub.getItem('refresh_token'),
  )
}

// ---------------------------------------------------------------- B: refresh 失败
{
  resetEnv('expired-token', 'refresh-bad')
  axiosStub.setAdapter(async (cfg) => {
    const url = String(cfg.url || '')
    if (url === '/auth/refresh') {
      axiosStub.stats.refresh += 1
      await delay(20)
      throw fail401('刷新令牌已失效')
    }
    throw fail401('登录已过期')
  })
  const settled = await Promise.allSettled([
    request.get('/topics'),
    request.get('/teacher/selections'),
    request.get('/notifications'),
  ])
  const rejected = settled.filter((s) => s.status === 'rejected')
  const notifyErrors = epStub.notifyLog.filter((n) => n.level === 'error')
  check(
    'B1 refresh 失败时 3 个请求都 reject',
    rejected.length === 3,
    '实测 reject ' + rejected.length + ' / 3',
  )
  check(
    'B2 refresh 仍只调用 1 次(失败也去重)',
    axiosStub.stats.refresh === 1,
    '实测 refresh 次数 = ' + axiosStub.stats.refresh,
  )
  check(
    'B3 清空 localStorage 的会话数据',
    localStorageStub.getItem('access_token') === null && localStorageStub.getItem('refresh_token') === null && localStorageStub.getItem('user_info') === null,
    'access_token=' + localStorageStub.getItem('access_token') + ', refresh_token=' + localStorageStub.getItem('refresh_token') + ', user_info=' + localStorageStub.getItem('user_info'),
  )
  check(
    'B4 跳转 /login 并带 redirect 回跳参数',
    navigations.length === 1 && navigations[0] === '/login?redirect=%2Fteacher%2Fhome%3Fpage%3D2',
    '实测 window.location.replace 调用: ' + JSON.stringify(navigations),
  )
  check(
    'B5 「登录已过期」只提示一次',
    notifyErrors.length === 1 && notifyErrors[0].message === session.SESSION_EXPIRED_MESSAGE,
    '实测提示 ' + notifyErrors.length + ' 次: ' + JSON.stringify(notifyErrors.map((n) => n.message)),
  )
}

// ---------------------------------------------------------------- C: 重放后仍 401
{
  resetEnv('expired-token', 'refresh-ok')
  const log = []
  axiosStub.setAdapter(async (cfg) => {
    const url = String(cfg.url || '')
    log.push(url)
    if (url === '/auth/refresh') {
      axiosStub.stats.refresh += 1
      return okBody({ access_token: 'fresh-token', refresh_token: 'refresh-2' })
    }
    throw fail401('登录已过期')
  })
  const settled = await Promise.allSettled([request.get('/topics')])
  const business = log.filter((u) => u !== '/auth/refresh' && u !== '/auth/profile')
  check(
    'C1 重放后仍 401 不会无限重试',
    business.length === 2 && axiosStub.stats.refresh === 1 && settled[0].status === 'rejected',
    '实测业务请求 ' + business.length + ' 次, refresh ' + axiosStub.stats.refresh + ' 次, 结果 ' + settled[0].status,
  )
  check(
    'C2 判断为会话失效 -> 清存储 + 只提示一次',
    localStorageStub.getItem('access_token') === null && epStub.notifyLog.filter((n) => n.level === 'error').length === 1,
    'access_token=' + localStorageStub.getItem('access_token') + ', 提示 ' + epStub.notifyLog.length + ' 次',
  )
}

// ---------------------------------------------------------------- D: 错误文案优先级
{
  check(
    'D1 后端 message 优先于 code 映射',
    errorUtils.resolveErrorMessage(40100, '旧密码不正确', 401) === '旧密码不正确',
    'resolveErrorMessage(40100, "旧密码不正确", 401) = ' + errorUtils.resolveErrorMessage(40100, '旧密码不正确', 401),
  )
  check(
    'D2 无后端 message 时走 code 映射',
    errorUtils.resolveErrorMessage(40300, '', 403) === '无权限访问该资源',
    'resolveErrorMessage(40300, "", 403) = ' + errorUtils.resolveErrorMessage(40300, '', 403),
  )
  check(
    'D3 未知 code 走 HTTP 状态映射',
    errorUtils.resolveErrorMessage(49999, '', 502) === errorUtils.HTTP_STATUS_MESSAGE[502],
    'resolveErrorMessage(49999, "", 502) = ' + errorUtils.resolveErrorMessage(49999, '', 502),
  )
  check(
    'D4 全部缺失时落兜底文案',
    errorUtils.resolveErrorMessage(null, null, null) === errorUtils.DEFAULT_ERROR_MESSAGE,
    'resolveErrorMessage(null, null, null) = ' + errorUtils.resolveErrorMessage(null, null, null),
  )
  check(
    'D5 FastAPI detail 数组也能取到 message',
    errorUtils.extractBackendMessage({ detail: [{ msg: '字段必填' }] }) === '字段必填',
    'extractBackendMessage({detail:[{msg:"字段必填"}]}) = ' + errorUtils.extractBackendMessage({ detail: [{ msg: '字段必填' }] }),
  )
}

// ---------------------------------------------------------------- E: JWT exp 判定
{
  const makeToken = (payload) => 'h.' + Buffer.from(JSON.stringify(payload)).toString('base64url') + '.s'
  const now = Math.floor(Date.now() / 1000)
  check('E1 已过期 token 判为过期', tokenUtils.isTokenExpired(makeToken({ exp: now - 60 })) === true, 'exp = now-60s')
  check('E2 未过期 token 判为有效', tokenUtils.isTokenExpired(makeToken({ exp: now + 3600 })) === false, 'exp = now+3600s')
  check('E3 30s 内即将过期也判为过期', tokenUtils.isTokenExpired(makeToken({ exp: now + 10 })) === true, 'exp = now+10s, skew=30s')
  check('E4 无 exp 时不擅自判过期', tokenUtils.isTokenExpired(makeToken({ sub: '1', role: 'TEACHER' })) === false, 'payload 无 exp')
  check(
    'E5 角色 -> 首页映射齐全',
    tokenUtils.roleHome('ADMIN') === '/admin/home' && tokenUtils.roleHome('TEACHER') === '/teacher/home' && tokenUtils.roleHome('STUDENT') === '/student/home' && tokenUtils.roleHome('UNKNOWN') === '/login',
    'ADMIN/TEACHER/STUDENT/UNKNOWN 均已验证',
  )
  check(
    'E6 decodeJwtPayload 能取出 role 与 exp',
    (() => { const p = tokenUtils.decodeJwtPayload(makeToken({ role: 'ADMIN', exp: now + 60 })); return p && p.role === 'ADMIN' && p.exp === now + 60 })(),
    'payload = ' + JSON.stringify(tokenUtils.decodeJwtPayload(makeToken({ role: 'ADMIN', exp: now + 60 }))),
  )
}

// ---------------------------------------------------------------- F: refresh 后补齐 user_info
{
  resetEnv('expired-token', 'refresh-ok')
  const before = JSON.parse(localStorageStub.getItem('user_info'))
  const synced = []
  session.registerProfileSync((user) => synced.push(user))
  const log = []
  axiosStub.setAdapter(async (cfg) => {
    const url = String(cfg.url || '')
    const auth = String((cfg.headers && cfg.headers.Authorization) || '')
    log.push({ url, auth })
    if (url === '/auth/refresh') {
      axiosStub.stats.refresh += 1
      return okBody({ access_token: 'fresh-token', refresh_token: 'refresh-2' })
    }
    if (url === '/auth/profile') {
      return okBody({ id: 2, role: 'TEACHER', real_name: '张老师', must_change_password: true })
    }
    if (auth === 'Bearer expired-token') throw fail401('登录已过期')
    return okBody({ route: url })
  })
  await request.get('/topics')
  const after = JSON.parse(localStorageStub.getItem('user_info'))
  const profileCalls = log.filter((e) => e.url === '/auth/profile')
  check(
    'F1 refresh 成功后自动重新拉取 /auth/profile',
    profileCalls.length === 1,
    '实测 /auth/profile 调用 ' + profileCalls.length + ' 次 (期望 1)',
  )
  check(
    'F2 /auth/profile 使用刷新后的新 token',
    profileCalls.length === 1 && profileCalls[0].auth === 'Bearer fresh-token',
    '实测 Authorization = ' + (profileCalls[0] ? profileCalls[0].auth : '无'),
  )
  check(
    'F3 user_info 被重写且 must_change_password 为最新值',
    before.must_change_password !== true && after.must_change_password === true,
    '刷新前 = ' + JSON.stringify(before) + ' / 刷新后 = ' + JSON.stringify(after),
  )
  check(
    'F4 最新用户信息同步进内存 store 回调',
    synced.length === 1 && synced[0].must_change_password === true,
    '实测回调 ' + synced.length + ' 次: ' + JSON.stringify(synced),
  )
  session.registerProfileSync(null)
}

// ---------------------------------------------------------------- G: SSE 流式接口的鉴权与 401 恢复
{
  const makeJwt = (payload) => 'h.' + Buffer.from(JSON.stringify(payload)).toString('base64url') + '.s'
  const nowSec = Math.floor(Date.now() / 1000)
  const originalFetch = globalThis.fetch

  resetEnv(makeJwt({ role: 'STUDENT', exp: nowSec - 60 }), 'refresh-ok')
  axiosStub.setAdapter(async (cfg) => {
    const url = String(cfg.url || '')
    if (url === '/auth/refresh') {
      axiosStub.stats.refresh += 1
      return okBody({ access_token: 'fresh-stream-token', refresh_token: 'refresh-2' })
    }
    if (url === '/auth/profile') return okBody({ id: 3, role: 'STUDENT', must_change_password: false })
    return okBody({})
  })
  const fetchCalls = []
  globalThis.fetch = async (url, init) => {
    const headers = init && init.headers
    fetchCalls.push({
      url: String(url),
      auth: headers && typeof headers.get === 'function' ? headers.get('Authorization') : null,
      ctype: headers && typeof headers.get === 'function' ? headers.get('Content-Type') : null,
    })
    return { status: 200, ok: true, body: null }
  }
  await sse.authorizedFetch('/api/v1/ai/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  check(
    'G1 SSE 前发现 token 过期 -> 先 refresh 再发起流式请求',
    axiosStub.stats.refresh === 1 && fetchCalls.length === 1,
    '实测 refresh ' + axiosStub.stats.refresh + ' 次, fetch ' + fetchCalls.length + ' 次',
  )
  check(
    'G2 流式请求携带刷新后的新 token',
    fetchCalls.length === 1 && fetchCalls[0].auth === 'Bearer fresh-stream-token',
    '实测 Authorization = ' + (fetchCalls[0] ? fetchCalls[0].auth : '无'),
  )
  check(
    'G3 保留调用方自己的 Content-Type 头',
    fetchCalls.length === 1 && fetchCalls[0].ctype === 'application/json',
    '实测 Content-Type = ' + (fetchCalls[0] ? fetchCalls[0].ctype : '无'),
  )

  resetEnv(makeJwt({ role: 'STUDENT', exp: nowSec + 3600 }), 'refresh-ok')
  axiosStub.setAdapter(async (cfg) => {
    if (String(cfg.url || '') === '/auth/refresh') {
      axiosStub.stats.refresh += 1
      return okBody({ access_token: 'should-not-be-used' })
    }
    return okBody({})
  })
  globalThis.fetch = async () => ({ status: 401, ok: false, body: null })
  let thrown = null
  try {
    await sse.authorizedFetch('/api/v1/ai/chat/stream', { method: 'POST' })
  } catch (e) {
    thrown = e
  }
  check(
    'G4 SSE 返回 401 -> 提示「登录已过期」并抛出可识别错误',
    epStub.notifyLog.filter((n) => n.level === 'error').length === 1 &&
      Boolean(thrown) &&
      thrown.message === session.SESSION_EXPIRED_MESSAGE,
    '实测提示 ' + epStub.notifyLog.filter((n) => n.level === 'error').length + ' 次, 抛出 = ' + (thrown && thrown.message),
  )
  check(
    'G5 SSE 返回 401 -> 清会话并跳转 /login',
    localStorageStub.getItem('access_token') === null &&
      navigations.length === 1 &&
      String(navigations[0]).startsWith('/login?redirect='),
    'access_token=' + localStorageStub.getItem('access_token') + ', 跳转 = ' + JSON.stringify(navigations),
  )
  check(
    'G6 token 有效时不被多余 refresh 干扰',
    axiosStub.stats.refresh === 0,
    '实测 refresh ' + axiosStub.stats.refresh + ' 次 (期望 0)',
  )
  globalThis.fetch = originalFetch
}
const failed = checks.filter((c) => !c.passed)
process.stdout.write('\n共 ' + checks.length + ' 项断言, 通过 ' + (checks.length - failed.length) + ' 项, 失败 ' + failed.length + ' 项\n')
if (failed.length) {
  process.stdout.write('失败项: ' + failed.map((c) => c.name).join(' | ') + '\n')
  process.exit(1)
}
process.stdout.write('结论: PASS — 401 恢复路径与并发去重符合 D49 要求\n')
