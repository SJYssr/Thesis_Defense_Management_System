#!/usr/bin/env node
/**
 * frontend/scripts/check-api-contract.mjs
 *
 * 静态解析前端 URL 字面量(含模板字符串与同文件常量表推导) + 后端路由注册表,
 * 做前缀级契约比对。不启动后端、不联网、零依赖。
 *
 *   node scripts/check-api-contract.mjs [--verbose]
 *
 * 退出码: 0 = 全部命中; 1 = 存在"前端调用了但后端没注册"或"方法不一致"。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BT = String.fromCharCode(96)
const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const ROOT = path.resolve(FRONTEND, '..')
const BACKEND_APP = path.join(ROOT, 'backend', 'app')
const SRC = path.join(FRONTEND, 'src')
const VERBOSE = process.argv.includes('--verbose')

// ---------------------------------------------------------------- 白名单
// 每条必须写清"为什么允许", 否则不算白名单, 只算隐蔽的漏检。
const WHITELIST = [
  {
    path: '/api/v1/ai/chat/stream',
    methods: ['POST'],
    reason:
      'SSE 流式问答必须用原生 fetch 才能逐块读取 (src/views/student/AiChat.vue:35); axios 无法流式消费, '
      + '所以刻意绕开 request.ts。后端 app/modules/ai/router.py 已注册 POST /ai/chat/stream。',
  },
]

// ---------------------------------------------------------------- 基础工具
function walk(dir, filter, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(p, filter, out)
    else if (filter(p)) out.push(p)
  }
  return out
}

const lineOf = (text, index) => text.slice(0, index).split('\n').length
const segs = (p) => p.split('/').filter(Boolean)
const isParam = (s) => s === '{param}'

function normalizePath(p) {
  let s = String(p).split('?')[0].split('#')[0].trim()
  s = s.replace(/\$\{[^}]*\}/g, '{param}')
  s = s.replace(/\{[^}]*\}/g, '{param}')
  s = s.replace(/:[A-Za-z_][A-Za-z0-9_]*/g, '{param}')
  s = s.replace(/\/+$/, '')
  return s || '/'
}

/** 左右逐段比对, 参数段视为通配 */
function pathMatches(front, back) {
  const a = segs(front)
  const b = segs(back)
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    if (isParam(a[i]) || isParam(b[i])) continue
    if (a[i].toLowerCase() !== b[i].toLowerCase()) return false
  }
  return true
}

// ---------------------------------------------------------------- 后端路由表
function readApiPrefix() {
  const main = path.join(BACKEND_APP, 'main.py')
  if (!fs.existsSync(main)) return '/api/v1'
  const text = fs.readFileSync(main, 'utf8')
  const m = /^API_PREFIX\s*=\s*settings\.(\w+)/m.exec(text)
  if (m) {
    const cfg = fs.readFileSync(path.join(BACKEND_APP, 'core', 'config.py'), 'utf8')
    const d = new RegExp('^\\s*' + m[1] + '\\s*:\\s*str\\s*=\\s*["\']([^"\']+)', 'm').exec(cfg)
    if (d) return d[1]
  }
  const lit = /^API_PREFIX\s*=\s*["']([^"']+)["']/m.exec(text)
  return lit ? lit[1] : '/api/v1'
}

function collectBackendRoutes() {
  const API_PREFIX = readApiPrefix()
  const files = walk(BACKEND_APP, (f) => f.endsWith('.py'))
  const routes = []
  const prefixTemplates = new Map()
  const instantiated = new Map()

  const factory = path.join(BACKEND_APP, 'modules', 'stages', 'router_factory.py')
  if (fs.existsSync(factory)) {
    const text = fs.readFileSync(factory, 'utf8')
    for (const m of text.matchAll(/def\s+(make_\w+?)_router\s*\(([^)]*)\)[\s\S]{0,400}?APIRouter\(\s*prefix=f?["']([^"']+)["']/g)) {
      if (m[3].includes('{')) prefixTemplates.set(m[1], m[3])
    }
  }
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8')
    for (const m of text.matchAll(/(make_(?:student|teacher|admin))_router\(\s*[^,]+,\s*["']([^"']+)["']\s*\)/g)) {
      const tpl = prefixTemplates.get(m[1])
      if (tpl) instantiated.set(m[1] + ':' + m[2], tpl.replace('{path}', m[2]))
    }
  }

  const factoryRoutes = []
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8')
    const rel = path.relative(ROOT, file)
    const isFactory = rel.endsWith('stages/router_factory.py')
    const scopeStarts = [...text.matchAll(/^def\s+(\w+)/gm)].map((m) => ({ name: m[1], at: m.index }))
    const scopeOf = (idx) => {
      let name = '<module>'
      for (const s of scopeStarts) { if (idx > s.at) name = s.name; else break }
      return name
    }
    const varPrefix = new Map()
    const factoryPrefix = new Map()
    for (const m of text.matchAll(/(\w+)\s*=\s*APIRouter\(([^()]*)\)/g)) {
      const p = /prefix\s*=\s*f?["']([^"']*)["']/.exec(m[2])
      if (isFactory) {
        const scope = scopeOf(m.index)
        const kind = /^make_(student|teacher|admin)_router$/.exec(scope)
        if (kind) factoryPrefix.set(scope, { kind: kind[1], tpl: p ? p[1] : '' })
      } else {
        varPrefix.set(m[1], p ? p[1] : '')
      }
    }
    if (!isFactory) {
      for (const m of text.matchAll(/(\w+)\s*=\s*(make_(?:student|teacher|admin))_router\(\s*[^,]+,\s*["']([^"']+)["']\s*\)/g)) {
        const got = instantiated.get(m[2] + ':' + m[3])
        if (got) varPrefix.set(m[1], got)
      }
    }
    for (const m of text.matchAll(/@(\w+)\.(get|post|put|delete|patch)\(\s*f?["']([^"']*)["']/g)) {
      const source = rel + ':' + lineOf(text, m.index)
      if (isFactory) {
        const info = factoryPrefix.get(scopeOf(m.index))
        if (!info) continue
        factoryRoutes.push({ kind: info.kind, prefixTpl: info.tpl, pathTpl: m[3], method: m[2].toUpperCase(), source })
        continue
      }
      const varName = m[1]
      if (!varPrefix.has(varName)) continue
      routes.push({
        method: m[2].toUpperCase(),
        path: normalizePath(API_PREFIX + (varPrefix.get(varName) || '') + m[3]),
        source,
      })
    }
  }
  // stages 工厂: 每个 make_*(cfg, path) 实例化一次 -> 把前缀模板逐一展开成具体路径
  for (const fr of factoryRoutes) {
    for (const [key, concrete] of instantiated) {
      const kind = key.split(':')[0]
      if (kind !== 'make_' + fr.kind) continue
      routes.push({
        method: fr.method,
        path: normalizePath(API_PREFIX + fr.prefixTpl.replace('{path}', key.split(':')[1]) + fr.pathTpl),
        source: fr.source + ' -> ' + concrete,
      })
    }
  }

  const main = path.join(BACKEND_APP, 'main.py')
  if (fs.existsSync(main)) {
    const text = fs.readFileSync(main, 'utf8')
    for (const m of text.matchAll(/@app\.(get|post|put|delete|patch)\(\s*f?["']([^"']*)["']/g)) {
      const raw = m[2].replace(/\{API_PREFIX\}/g, API_PREFIX)
      routes.push({ method: m[1].toUpperCase(), path: normalizePath(raw), source: 'backend/app/main.py:' + lineOf(text, m.index) })
    }
  }
  return { API_PREFIX, routes }
}

// ---------------------------------------------------------------- 前端: 字面量 / 模板 / 常量表推导
function readStringLiteral(text, i) {
  const quote = text[i]
  if (quote !== "'" && quote !== '"') return null
  let j = i + 1
  let value = ''
  while (j < text.length) {
    const ch = text[j]
    if (ch === '\\') { value += text[j + 1] ?? ''; j += 2; continue }
    if (ch === quote) return { value, end: j + 1 }
    if (ch === '\n') return null
    value += ch
    j += 1
  }
  return null
}

/** 读 template literal: 切成 literal 段与插值表达式段, 插值段记为 expr */
function readTemplate(text, i) {
  if (text[i] !== BT) return null
  let j = i + 1
  let lit = ''
  const parts = []
  while (j < text.length) {
    const ch = text[j]
    if (ch === '\\') { lit += text[j + 1] ?? ''; j += 2; continue }
    if (ch === BT) { if (lit) parts.push({ lit }); return { parts, end: j + 1 } }
    if (ch === '$' && text[j + 1] === '{') {
      if (lit) { parts.push({ lit }); lit = '' }
      let k = j + 2
      let depth = 1
      let expr = ''
      while (k < text.length) {
        const c = text[k]
        if (c === "'" || c === '"') {
          const s = readStringLiteral(text, k)
          if (!s) break
          expr += text.slice(k, s.end)
          k = s.end
          continue
        }
        if (c === '{') depth += 1
        if (c === '}') { depth -= 1; if (depth === 0) break }
        expr += c
        k += 1
      }
      parts.push({ expr })
      j = k + 1
      continue
    }
    lit += ch
    j += 1
  }
  return null
}

/** 从 i 起读一个实参表达式, 停在顶层逗号或右括号 */
function readArgExpression(text, i) {
  let j = i
  let depth = 0
  let out = ''
  while (j < text.length) {
    const ch = text[j]
    if (ch === "'" || ch === '"') { const s = readStringLiteral(text, j); if (!s) break; out += text.slice(j, s.end); j = s.end; continue }
    if (ch === BT) { const s = readTemplate(text, j); if (!s) break; out += text.slice(j, s.end); j = s.end; continue }
    if ('([{<'.includes(ch)) depth += 1
    if (')]}'.includes(ch)) { if (depth === 0) break; depth -= 1 }
    if (ch === '>' && depth > 0) depth -= 1
    if (ch === ',' && depth === 0) break
    out += ch
    j += 1
  }
  return { expr: out.trim(), end: j }
}

/** 极简对象字面量解析: 只认 key: 字符串 与嵌套对象 */
function parseObjectLiteral(text, i) {
  let j = i + 1
  const obj = {}
  while (j < text.length) {
    while (j < text.length && /[\s,]/.test(text[j])) j += 1
    if (text[j] === '}') return { obj, end: j + 1 }
    let key = null
    const lit = readStringLiteral(text, j)
    if (lit) { key = lit.value; j = lit.end } else {
      const m = /^[A-Za-z_$][\w$]*/.exec(text.slice(j))
      if (!m) return { obj, end: j }
      key = m[0]
      j += m[0].length
    }
    while (j < text.length && /\s/.test(text[j])) j += 1
    if (text[j] !== ':') return { obj, end: j }
    j += 1
    while (j < text.length && /\s/.test(text[j])) j += 1
    if (text[j] === '{') {
      const sub = parseObjectLiteral(text, j)
      obj[key] = sub.obj
      j = sub.end
      continue
    }
    const s = readStringLiteral(text, j)
    if (s) { obj[key] = s.value; j = s.end; continue }
    if (text[j] === BT) { const t = readTemplate(text, j); if (t) { obj[key] = null; j = t.end; continue } }
    while (j < text.length && text[j] !== ',' && text[j] !== '}') j += 1
    obj[key] = null
  }
  return { obj, end: j }
}

function collectConstMaps(text) {
  const maps = new Map()
  for (const m of text.matchAll(/const\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*\{/g)) {
    const start = m.index + m[0].length - 1
    if (text[start] !== '{') continue
    const parsed = parseObjectLiteral(text, start)
    if (parsed.obj && Object.keys(parsed.obj).length) maps.set(m[1], parsed.obj)
  }
  return maps
}

const leafStrings = (node, out = []) => {
  if (typeof node === 'string') out.push(node)
  else if (node && typeof node === 'object') for (const v of Object.values(node)) leafStrings(v, out)
  return out
}

/** 表达式 -> 候选路径数组; 推导不出返回 null */
function resolveExpression(expr, maps) {
  const raw = String(expr).trim().replace(/^\(+/, '').replace(/\)+$/, '').trim()
  const m = /^([A-Za-z_$][\w$]*)(?:\[[^\]]*\])?((?:\.[A-Za-z_$][\w$]*)*)$/.exec(raw)
  if (!m) return null
  const base = maps.get(m[1])
  if (!base) return null
  const props = m[2].split('.').filter(Boolean)
  const out = []
  for (const branch of Object.values(base)) {
    let node = branch
    let ok = true
    for (const p of props) {
      if (node && typeof node === 'object' && p in node) node = node[p]
      else { ok = false; break }
    }
    if (!ok) continue
    if (typeof node === 'string') out.push(node)
    else leafStrings(node, out)
  }
  return out.length ? [...new Set(out)] : null
}

/** 模板段数组或表达式串 -> 候选 URL 数组 */
function expandTargets(partsOrExpr, maps) {
  if (typeof partsOrExpr === 'string') return resolveExpression(partsOrExpr, maps)
  let acc = ['']
  for (const part of partsOrExpr) {
    if (part.lit !== undefined) { acc = acc.map((s) => s + part.lit); continue }
    const resolved = resolveExpression(part.expr, maps)
    const options = resolved ?? ['{param}']
    const next = []
    for (const prefix of acc) for (const opt of options) next.push(prefix + opt)
    acc = next.slice(0, 80)
  }
  return acc
}

/** 跳过泛型实参与空白, 返回左括号下标; -1 表示不是调用 */
function findArgParen(text, i) {
  let j = i
  while (j < text.length && /\s/.test(text[j])) j += 1
  if (text[j] === '<') {
    let depth = 0
    while (j < text.length) {
      if (text[j] === '<') depth += 1
      else if (text[j] === '>') { depth -= 1; if (depth === 0) { j += 1; break } }
      j += 1
    }
    while (j < text.length && /\s/.test(text[j])) j += 1
  }
  return text[j] === '(' ? j : -1
}

function collectFrontendCalls() {
  const files = walk(SRC, (f) => f.endsWith('.ts') || f.endsWith('.vue'))
  const calls = []
  const unparsed = []
  const clients = ['request', 'axios', 'refreshClient']
  const methods = ['get', 'post', 'put', 'delete', 'patch']

  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8')
    const rel = path.relative(FRONTEND, file)
    const maps = collectConstMaps(text)
    const re = new RegExp('\\b(' + clients.join('|') + ')\\.(' + methods.join('|') + ')\\b', 'g')

    for (const m of text.matchAll(re)) {
      const paren = findArgParen(text, m.index + m[0].length)
      if (paren === -1) continue
      let k = paren + 1
      while (k < text.length && /\s/.test(text[k])) k += 1
      const call = { method: m[2].toUpperCase(), file: rel, line: lineOf(text, m.index), targets: null, how: '' }

      const lit = readStringLiteral(text, k)
      const tpl = lit ? null : readTemplate(text, k)
      if (lit) { call.targets = [lit.value]; call.how = '字面量' }
      else if (tpl) { call.targets = expandTargets(tpl.parts, maps); call.how = '模板字符串' }
      else {
        const arg = readArgExpression(text, k)
        const resolved = resolveExpression(arg.expr, maps)
        if (resolved) { call.targets = resolved; call.how = '常量表推导: ' + arg.expr }
      }

      if (!call.targets || !call.targets.length) {
        unparsed.push({ file: rel, line: call.line, raw: m[0] + '(...)' })
        continue
      }
      calls.push(call)
    }

    for (const m of text.matchAll(/\b(fetch|authorizedFetch)\s*\(/g)) {
      const callee = m[1]
      // sse.ts 里的 fetch 是鉴权包装器内部透传(input 由调用方传入), 不是真实调用点
      if (callee === 'fetch' && rel === 'src/api/sse.ts') continue
      // 函数声明本身不是调用点
      if (/function\s+$/.test(text.slice(Math.max(0, m.index - 12), m.index))) continue
      let k = m.index + m[0].length
      while (k < text.length && /\s/.test(text[k])) k += 1
      const lit = readStringLiteral(text, k)
      const tpl = lit ? null : readTemplate(text, k)
      if (!lit && !tpl) { unparsed.push({ file: rel, line: lineOf(text, m.index), raw: callee + '(...)' }); continue }
      const after = text.slice((lit ? lit.end : tpl.end), (lit ? lit.end : tpl.end) + 400)
      const method = /method\s*:\s*['"](\w+)['"]/.exec(after)
      calls.push({
        method: (method ? method[1] : 'GET').toUpperCase(),
        file: rel,
        line: lineOf(text, m.index),
        targets: lit ? [lit.value] : expandTargets(tpl.parts, maps),
        how: (lit ? '原生 ' : '原生 ') + callee + (lit ? ' 字面量' : ' 模板'),
      })
    }
  }
  return { calls, unparsed }
}

// ---------------------------------------------------------------- 比对
const { API_PREFIX, routes } = collectBackendRoutes()
const { calls, unparsed } = collectFrontendCalls()

const declared = new Map()
for (const r of routes) declared.set(r.method + ' ' + r.path, r)

const fullPath = (url) => normalizePath(url.startsWith('/api/') ? url : API_PREFIX + url)
const whitelisted = []
const matched = []
const violations = []
const methodMismatch = []
const usedKeys = new Set()

for (const call of calls) {
  const targets = [...new Set(call.targets.map(fullPath))]
  const missing = []
  const hits = []
  for (const p of targets) {
    const direct = declared.get(call.method + ' ' + p)
    const soft = direct || routes.find((r) => r.method === call.method && pathMatches(p, r.path))
    if (soft) { hits.push(soft); continue }
    const otherMethod = routes.filter((r) => pathMatches(p, r.path))
    if (otherMethod.length) { methodMismatch.push({ call, path: p, candidates: otherMethod }); continue }
    missing.push(p)
  }
  if (missing.length) {
    const wl = WHITELIST.find((w) => targets.every((p) => normalizePath(w.path) === p) && w.methods.includes(call.method))
    if (wl) whitelisted.push({ call, path: targets.join(', '), reason: wl.reason })
    else violations.push({ call, missing })
    continue
  }
  if (hits.length) {
    matched.push({ call, targets, route: hits[0], hits: hits.length })
    for (const h of hits) usedKeys.add(h.method + ' ' + h.path)
  }
}

// ---------------------------------------------------------------- 输出
const line = (s = '') => process.stdout.write(s + '\n')

line('===== 前端 -> 后端 契约静态比对 =====')
line('后端 API 前缀: ' + API_PREFIX)
line('后端已注册路由: ' + routes.length + ' 条 | 前端调用点: ' + calls.length + ' 处 | 白名单: ' + WHITELIST.length + ' 条')
line()

line('--- 命中 (' + matched.length + ') ---')
if (VERBOSE) {
  for (const item of matched) {
    const tp = item.call.targets.length > 1 ? '  [展开 ' + item.hits + ' 条候选]' : ''
    line('  OK  ' + item.call.method.padEnd(6) + item.targets.map(fullPath).join(' | ') + '  <- ' + item.call.file + ':' + item.call.line + '  -> ' + item.route.path + tp)
  }
} else {
  line('  ' + matched.length + ' 处全部命中同方法同路径的注册 (--verbose 逐条打印)')
}
line()

if (whitelisted.length) {
  line('--- 白名单放行 (' + whitelisted.length + ') ---')
  for (const w of whitelisted) {
    line('  WL  ' + w.call.method.padEnd(6) + w.path + '  <- ' + w.call.file + ':' + w.call.line)
    line('      理由: ' + w.reason)
  }
  line()
}

if (methodMismatch.length) {
  line('--- 方法不一致 (' + methodMismatch.length + ') ---')
  for (const v of methodMismatch) {
    line('  !!  ' + v.call.method + ' ' + v.path + '  <- ' + v.call.file + ':' + v.call.line)
    for (const c of v.candidates) line('      后端只有 ' + c.method + ' ' + c.path + '  (' + c.source + ')')
  }
  line()
}

if (violations.length) {
  line('--- 前端调用了但后端没注册 (' + violations.length + ') ---')
  for (const v of violations) {
    line('  XX  ' + v.call.method.padEnd(6) + '  <- ' + v.call.file + ':' + v.call.line + '  [' + v.call.how + ']')
    for (const p of v.missing) line('      未注册路径: ' + p)
  }
  line()
} else {
  line('--- 前端调用了但后端没注册: 0 条 ---')
  line()
}

if (unparsed.length) {
  line('--- 无法静态解析(人工核对) (' + unparsed.length + ') ---')
  for (const u of unparsed) line('  ??  ' + u.raw + '  <- ' + u.file + ':' + u.line)
  line()
}

const unused = routes.filter((r) => !usedKeys.has(r.method + ' ' + r.path)
  && !whitelisted.some((w) => pathMatches(normalizePath(w.path), r.path)))
line('--- 后端已注册但前端未调用 (参考项, 不算失败): ' + unused.length + ' 条 ---')
if (VERBOSE) for (const r of unused) line('  --  ' + r.method.padEnd(6) + r.path + '  (' + r.source + ')')
line()

// ---------------------------------------------------------------- 响应形状断言
// 目的: 后端一旦悄悄改变响应形状(数组→分页对象、字段改名、新增错误码)或前端退回
// 旧写法, 这里必须变红。只读前端源码特征串, 不启动后端。
const readSrc = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8')

const SHAPE_ASSERTIONS = [
  {
    name: 'stage-task 列表按钮只按 allowed_actions 渲染, 不用 status 推导',
    file: 'views/teacher/TaskManage.vue',
    must: [/row\.allowed_actions/, /canDo\(row, 'publish'\)/, /canDo\(row, 'close'\)/],
    mustNot: [/v-if="row\.status === 'DRAFT'"/, /v-if="row\.status === 'PUBLISHED'"/],
  },
  {
    name: 'POST /stage-tasks 显式传 status + PUT 不再夹带 status',
    file: 'views/teacher/TaskManage.vue',
    must: [/taskApi\.create\(\{ \.\.\.base, status: form\.status \}\)/, /taskApi\.update\(editing\.value\.id, base\)/],
  },
  {
    name: 'stage-task 版本历史按分页对象取值并显式传 page/page_size',
    file: 'views/teacher/TaskManage.vue',
    must: [/taskApi\.versions\(t\.id, \{ page: 1, page_size: 20 \}\)/, /res\.data\?\.items \?\? \[\]/],
    mustNot: [/versions\.value = res\.data \|\| \[\]/],
  },
  {
    name: 'stage-task publish / close 端点已在 api 层注册',
    file: 'api/defense.ts',
    must: [/\/stage-tasks\/\$\{id\}\/publish/, /\/stage-tasks\/\$\{id\}\/close/],
  },
  {
    name: '学年配置按分页对象取值并渲染分页器',
    file: 'views/admin/AcademicSchedule.vue',
    must: [/PageResult<Schedule>/, /payload\?\.items \?\? \[\]/, /<el-pagination/],
    mustNot: [/page_size: 100 \}\)/],
  },
  {
    name: '上传透传 business_type/business_id, 且 40013(当日配额) 与 40010(单文件超限) 分开映射',
    files: ['api/common.ts', 'api/business.ts', 'utils/error.ts'],
    must: [/business_type/, /business_id/, /40013: '今日上传配额已用尽/, /40010: '单文件超出大小限制/],
  },
  {
    name: '公告支持 status=DRAFT|PUBLISHED, 并注明 DELETE=下线(transition offline)',
    file: 'api/common.ts',
    must: [/status\?: 'DRAFT' \| 'PUBLISHED'/, /transition\(offline\)/],
  },
  {
    name: 'dashboard stats: pass_counts 按 {name,value,total} 自算比率, 口径标注读 scope_map 不写死当前学年',
    files: ['views/dashboards/AdminHome.vue', 'views/admin/AdminStatistics.vue'],
    must: [/pass_counts/, /value \/ row\.total/, /scopeLabel\(/],
    mustNot: [
      /label: '材料审核完成'/,
      /label: '累计完成'/,
      // P8: 口径由 scope_map 决定, 角标不允许写死
      /<span class="batch-select">当前学年<\/span>/,
      /<span class="head-select">当前学年<\/span>/,
    ],
  },
  {
    name: 'dashboard stats: student_total / selected_total 已进类型并被展示',
    files: ['types/api.ts', 'views/dashboards/AdminHome.vue'],
    must: [/student_total\?: number/, /selected_total\?: number/, /yearTotals\.student_total/, /yearTotals\.selected_total/],
  },
  {
    name: 'progress summary 缺省即当前学年(页面不再写全部学年)',
    file: 'views/admin/AdminProgress.vue',
    must: [/query\.year \|\| '当前学年'/, /placeholder="当前学年"/],
    mustNot: [/全部学年/],
  },
  {
    name: 'overdue-scan 按 mode 区分提示, celery 路径不自称扫描 0 条',
    files: ['views/admin/AdminProgress.vue', 'api/business.ts'],
    must: [/data\.mode === 'celery'/, /已提交后台扫描/, /\/admin\/progress\/overdue-scan/],
    mustNot: [/scanned \?\? 0 条/],
  },
  {
    name: 'max_upload_daily_mb 进入系统设置页',
    file: 'views/admin/SystemSettings.vue',
    must: [/max_upload_daily_mb/],
  },
  {
    name: 'axios 泛型一律 ApiEnvelope<T>(响应拦截器返回整个信封, R 写成业务类型会让运行期 .data 错位)',
    dir: 'api',
    must: [],
    mustNot: [/request\.(get|post|put|delete|patch)<unknown, (?!ApiEnvelope)/],
  },
  {
    name: '公告管理页接真接口(adminList/create/update/remove, 整页无 RAW_ROWS 假数据)',
    file: 'views/admin/AnnouncementAdmin.vue',
    must: [
      /announcementApi\.adminList\(/,
      /announcementApi\.create\(/,
      /announcementApi\.update\(/,
      /announcementApi\.remove\(/,
    ],
    mustNot: [/RAW_ROWS/],
  },
  {
    name: '公告页不展示后端不存在的字段(type/views/scope 三列为假字段)',
    file: 'views/admin/AnnouncementAdmin.vue',
    must: [/prop="title"/, /priority/, /publisher_name/, /expire_time/],
    mustNot: [
      /\brow\.views\b/,
      /\brow\.type\b/,
      /\brow\.scope\b/,
      /prop="(views|scope|type)"/,
      /\bviews:\s*\d/,
      /scope: \[/,
    ],
  },
  {
    name: 'reviewList 单请求下推(无 page_size=PAGE_SIZE 全量翻页 / worker pool / onProgress)',
    file: 'utils/reviewList.ts',
    must: [/keyword/, /date_from/, /date_to/, /loader\(buildReviewParams\(query, options\)\)/],
    mustNot: [/page_size: PAGE_SIZE/, /MAX_CONCURRENCY/, /onProgress/, /new Array\(/, /Array\.from\(\{ length/],
  },
  // 详情/表单统一弹窗展示: 五个页面曾把详情常驻在右侧 aside, 占掉列表宽度
  {
    name: '审核详情用弹窗(无常驻右侧 aside.detail-panel)',
    files: [
      'views/teacher/StageReview.vue', 'views/teacher/MaterialReview.vue',
      'views/teacher/SelectionReview.vue', 'views/teacher/StudentProgress.vue',
    ],
    must: [/<el-dialog v-model="detailVisible"/, /destroy-on-close/],
    mustNot: [/<aside class="detail-panel">/, /<el-empty v-if="!current" description="请选择左侧记录查看详情"/],
  },
  {
    name: '题目新增/编辑用弹窗(无常驻 aside.topic-form-panel)',
    file: 'views/teacher/MyTopics.vue',
    must: [/<el-dialog v-model="formVisible"/],
    mustNot: [/<aside class="topic-form-panel">/],
  },
  // 个人中心: 侧栏必须是单层直链, 不能再出现"个人信息/修改密码"二级分组
  {
    name: '个人中心是侧栏单层直链(无个人信息/修改密码二级分组)',
    file: 'layouts/DefaultLayout.vue',
    must: [/\{ title: '个人中心', icon: 'userbox', path: '\/profile' \}/],
    mustNot: [/title: '个人信息', path: '\/profile'/, /title: '修改密码', path: '\/profile'/],
  },
  // 个人中心表单宽度必须自适应: 固定 560px + 96px 标签 + 64px 内边距 = 720px 最小需求, 窄容器会顶出卡片
  {
    name: '个人中心表单宽度自适应(不写固定像素宽)',
    file: 'views/common/ProfileView.vue',
    must: [/\.pwd-form :deep\(\.el-input\) \{\s*width: 100%;/, /max-width: 560px/, /@media \(max-width: 1024px\)/],
    mustNot: [/width: 560px;\s*flex-shrink: 0;/, /flex: 0 0 560px;/, /white-space: nowrap/],
  },
  // 上传 accept 必须由后端策略生成: 曾经前端写死 'accept: \'.mp4,.zip\'' 而后端白名单没有 .mp4,
  // "演示视频"整类材料永远传不上去; 'accept: \'*\'' 也不是合法 HTML accept 值。
  {
    name: '上传 accept 由 /files/upload-policy 生成(不硬编码扩展名清单)',
    files: ['views/student/MaterialView.vue', 'api/common.ts'],
    must: [/uploadApi\.policy\(\)/, /acceptOf\(t\)/, /by_kind\[k\]/],
    mustNot: [/accept: '\*'/, /accept: '\.mp4/, /accept: '\.pdf/],
  },
  {
    name: '上传前做扩展名与大小预校验(不把错误全推给后端)',
    file: 'views/student/MaterialView.vue',
    must: [/function precheck\(/, /allowed_extensions\.includes\(ext\)/, /max_upload_size_mb/],
  },
  // P9: 学生自助归档必须走学生端入口。管理员用的 POST /archive-packages 是 require_admin
  // 且内部直接 approve → ARCHIVED, 学生拿它当提交入口会 40300 且绕过"待学院确认"这一环。
  {
    name: '学生归档提交走 /student/archive/packages(不拿管理员 /archive-packages create 当学生入口)',
    files: ['api/defense.ts', 'views/student/MyArchive.vue'],
    must: [
      /\/student\/archive\/eligibility/,
      /\/student\/archive\/packages/,
      /archiveApi\.submitStudent\(/,
      /archiveApi\.eligibility\(/,
    ],
    mustNot: [/archiveApi\.create\(/],
  },
  // P9: 归档上传与材料上传同一口径 —— accept 由 /files/upload-policy 下发, 上传前先 precheck。
  // accept 一旦在前端写死清单, 后端白名单调整后该类文件就会永久传不上去(见 MaterialView 的历史 bug)。
  {
    name: '归档上传 accept 由 uploadApi.policy() 生成且上传前 precheck(与 MaterialView 同口径)',
    file: 'views/student/MyArchive.vue',
    must: [
      /uploadApi\.policy\(\)/,
      /acceptOf\(/,
      /by_kind/,
      /function precheck\(/,
      /allowed_extensions\.includes\(ext\)/,
      /max_upload_size_mb/,
    ],
    mustNot: [/accept: '\*'/, /accept: '\.pdf/, /accept: '\.mp4/],
  },
  // P9: 归档提交表单放弹窗, 不要常驻 aside 把列表宽度吃掉(与审核页/题目页同一处理方式)。
  {
    name: '归档提交用弹窗(无常驻 aside 表单)',
    file: 'views/student/MyArchive.vue',
    must: [/<el-dialog v-model="submitVisible"/, /destroy-on-close/],
    mustNot: [/<aside/, /archive-form-panel/],
  },
  {
    name: '管理端归档有待确认入口(学生提交后必须有人确认才能生效)',
    file: 'views/admin/ArchiveAdmin.vue',
    must: [/archiveApi\.approve\(/, /row\.status === 'PENDING'/, /value="PENDING"/],
    mustNot: [/archiveApi\.create\(\s*\{\s*cycle_id: row/],
  },
  // P10: Agent 多步问答新增了 step / citations 事件。前端若不消费它们, 界面上就是"看不到
  // 思考过程、引用核验白做"; 若把未知事件当异常抛, 后端加一个事件就整条聊天崩掉。
  {
    name: 'AiChat 解析 step / citations 事件且末尾有 default 忽略未知事件',
    file: 'views/student/AiChat.vue',
    must: [/case 'step':/, /msg\.steps\.push\(/, /case 'citations':/, /msg\.citations = \{/, /case 'done':/, /default:/],
    mustNot: [/未知事件[^\n]*throw new Error/],
  },
  // P10: 单轮路径(old 只有 meta/delta/done)不产生 step, 思考区块必须"没 step 就不渲染",
  // 且默认折叠 —— 默认展开会把正文挤到屏幕外, 折叠状态靠 openSteps 空数组表达。
  {
    name: 'AiChat 思考过程默认折叠且仅在收到 step 时渲染(单轮路径无空区块)',
    file: 'views/student/AiChat.vue',
    must: [/<el-collapse v-if="m\.steps\.length"/, /v-model="m\.openSteps"/, /openSteps: \[\]/, /stepTitle\(/],
    mustNot: [/<el-collapse v-model="m\.openSteps">/],
  },
  // P10: agent 是可选请求字段(null 用服务端默认), 前端显式发布尔值, 默认开启多步检索。
  {
    name: 'AiChat 请求体带 agent 开关(默认开启多步检索)',
    file: 'views/student/AiChat.vue',
    must: [/const agentEnabled = ref\(true\)/, /agent: agentEnabled\.value/],
  },
  // P10: 管理员要能在界面填第三方 OpenAI 兼容服务的 Key —— 三组键必须真的绑定到表单,
  // 且 Key 输入是 password(明文展示等于把密钥贴到屏幕上), 掩码由 placeholder 说明。
  {
    name: '系统设置含 AI 服务表单(三组键齐全, api_key 用 password 且不回显明文)',
    file: 'views/admin/SystemSettings.vue',
    must: [
      /ai_llm_base_url/,
      /ai_llm_api_key/,
      /ai_embedding_dim/,
      /ai_rerank_model_dir/,
      /type="password"/,
      /show-password/,
      /已配置\(保存后覆盖\)/,
    ],
    mustNot: [/console\.(log|info|debug)\([^\n]*ai_/],
  },
  // P10: 密钥字段后端只回掩码。把整张表单(含掩码)提交回去, 轻则被后端当"不修改"忽略,
  // 重则把 ***abcd 写进库。保存必须只提交变化项, 且掩码/空值一律跳过。
  {
    name: '系统设置保存只提交变化字段(未修改/掩码/空的 api_key 不进请求体)',
    files: ['views/admin/SystemSettings.vue', 'utils/aiService.ts'],
    must: [
      /collectAiConfigChanges\(aiForm, aiBaseline\.value\)/,
      /isMaskedValue\(/,
      /skippedSecrets/,
      /if \(!next \|\| isMaskedValue\(next\) \|\| next === before\)/,
      /MAX_ITEMS_PER_PUT/,
    ],
    mustNot: [/items\[key\] = String\(value\)/],
  },
  // P10: 未配置/降级不许静默 —— 页头状态带 + 逐段探针必须都在界面上。
  {
    name: '系统设置页头状态带与测试连接(未配置/降级必须可见)',
    file: 'views/admin/SystemSettings.vue',
    must: [
      /尚未配置 AI 服务, AI 问答暂不可用/,
      /AI 服务异常/,
      /healthApi\.ai\(\)/,
      /testConnection/,
      /:loading="testing"/,
    ],
  },
]

function assertionSources(assertion) {
  if (assertion.dir) {
    const dir = path.join(SRC, assertion.dir)
    return fs.readdirSync(dir).filter((f) => f.endsWith('.ts'))
      .map((f) => ({ rel: assertion.dir + '/' + f, text: fs.readFileSync(path.join(dir, f), 'utf8') }))
  }
  return (assertion.files || [assertion.file]).map((rel) => ({ rel, text: readSrc(rel) }))
}

const shapeFailures = []
line('--- 响应形状断言 (' + SHAPE_ASSERTIONS.length + ' 条) ---')
for (const assertion of SHAPE_ASSERTIONS) {
  const sources = assertionSources(assertion)
  const missing = (assertion.must || []).filter((re) => !sources.some((s) => re.test(s.text)))
  const forbidden = []
  for (const re of assertion.mustNot || []) {
    for (const s of sources) if (re.test(s.text)) forbidden.push(re.source + ' @ ' + s.rel)
  }
  if (!missing.length && !forbidden.length) {
    line('  OK  ' + assertion.name)
    continue
  }
  shapeFailures.push(assertion.name)
  line('  XX  ' + assertion.name)
  for (const re of missing) line('      缺少特征: ' + re.source)
  for (const f of forbidden) line('      出现禁用特征: ' + f)
}
line()
const failed = violations.length + methodMismatch.length + shapeFailures.length
line(failed === 0
  ? '结论: PASS — 所有前端调用都能在后端路由表里找到同方法同路径的注册, 且 ' + SHAPE_ASSERTIONS.length + ' 条响应形状断言全部满足。'
  : '结论: FAIL — ' + violations.length + ' 条未注册, ' + methodMismatch.length + ' 条方法不一致, ' + shapeFailures.length + ' 条形状断言失败。')

process.exit(failed === 0 ? 0 : 1)
