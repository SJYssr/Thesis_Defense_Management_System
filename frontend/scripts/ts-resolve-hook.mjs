/**
 * 解析钩子: 把 @/ 映射到 frontend/src，并把 axios / element-plus 换成同目录下的 stub。
 * 用 node 原生 module.register 注册，不引入任何构建工具。
 */
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const SRC = path.join(FRONTEND, 'src')
const SRC_URL = pathToFileURL(SRC).href
const STUBS = {
  axios: path.join(HERE, 'axios-stub.mjs'),
  'element-plus': path.join(HERE, 'element-plus-stub.mjs'),
}

function hit(candidate) {
  try {
    return fs.existsSync(candidate) && fs.statSync(candidate).isFile()
  } catch {
    return false
  }
}

export async function resolve(specifier, context, nextResolve) {
  if (STUBS[specifier]) {
    return { url: pathToFileURL(STUBS[specifier]).href, shortCircuit: true, format: 'module' }
  }
  let target = null
  if (specifier.startsWith('@/')) {
    target = path.join(SRC, specifier.slice(2))
  } else if (specifier.startsWith('.') && context.parentURL && context.parentURL.startsWith(SRC_URL)) {
    target = path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier)
  }
  if (target) {
    const cands = [target, target + '.ts', target + '.mts', path.join(target, 'index.ts')]
    for (const cand of cands) {
      if (hit(cand)) return { url: pathToFileURL(cand).href, shortCircuit: true }
    }
  }
  return nextResolve(specifier, context)
}
