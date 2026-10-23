/**
 * 最小 axios 替身: 只实现 request.ts / session.ts 用到的那部分语义
 * (create / interceptors.use / request 调用链 / adapter 注入 / 401 error.config+error.response)。
 * 目的是让真实的拦截器代码在 Node 里跑起来，而不是被测逻辑被 mock 掉。
 */
export const stats = { refresh: 0, requests: [], adapterCalls: 0 }

let adapter = null

export function setAdapter(fn) {
  adapter = fn
}

export function resetStats() {
  stats.refresh = 0
  stats.requests = []
  stats.adapterCalls = 0
}

function manager() {
  const handlers = []
  return {
    handlers,
    use(fulfilled, rejected) {
      handlers.push({ fulfilled, rejected })
      return handlers.length - 1
    },
    eject(id) {
      if (handlers[id]) handlers[id] = { fulfilled: null, rejected: null }
    },
  }
}

export class AxiosError extends Error {
  constructor(message, config, response) {
    super(message)
    this.name = 'AxiosError'
    this.isAxiosError = true
    this.config = config
    this.response = response
  }
}

function makeResponse(config, status, data) {
  return { data, status, statusText: String(status), headers: {}, config, request: {} }
}

function dispatch(cfg) {
  return new Promise((resolve, reject) => {
    if (typeof adapter !== 'function') {
      reject(new Error('axios-stub: adapter 未注入'))
      return
    }
    stats.adapterCalls += 1
    Promise.resolve()
      .then(() => adapter(cfg))
      .then(
        (partial) => {
          const res = makeResponse(cfg, partial.status == null ? 200 : partial.status, partial.data)
          resolve(res)
        },
        (err) => {
          const res = err && err.response
            ? makeResponse(cfg, err.response.status, err.response.data)
            : null
          reject(new AxiosError((err && err.message) || 'adapter error', cfg, res))
        },
      )
  })
}

function createInstance(defaults) {
  const instance = (config) => instance.request(config)
  instance.defaults = Object.assign({ headers: {} }, defaults || {})
  instance.interceptors = { request: manager(), response: manager() }

  instance.request = (config) => {
    const cfg = Object.assign({}, instance.defaults, config || {})
    cfg.headers = Object.assign({}, instance.defaults.headers, (config && config.headers) || {})
    cfg.url = cfg.url || ''
    cfg.baseURL = cfg.baseURL || ''
    let promise = Promise.resolve(cfg)
    const reqHandlers = instance.interceptors.request.handlers
    for (let i = reqHandlers.length - 1; i >= 0; i -= 1) {
      const h = reqHandlers[i]
      promise = promise.then(h.fulfilled || undefined, h.rejected || undefined)
    }
    promise = promise.then((finalCfg) => dispatch(Object.assign({}, finalCfg, cfg, { headers: finalCfg.headers })))
    for (const h of instance.interceptors.response.handlers) {
      promise = promise.then(h.fulfilled || undefined, h.rejected || undefined)
    }
    return promise
  }

  for (const method of ['get', 'delete', 'head', 'options']) {
    instance[method] = (url, config) => instance.request(Object.assign({}, config || {}, { url, method }))
  }
  for (const method of ['post', 'put', 'patch']) {
    instance[method] = (url, data, config) =>
      instance.request(Object.assign({}, config || {}, { url, method, data }))
  }
  return instance
}

const axios = {
  create: (defaults) => createInstance(defaults),
  isAxiosError: (e) => Boolean(e && e.isAxiosError),
  AxiosError,
  defaults: { headers: {} },
}

export default axios
