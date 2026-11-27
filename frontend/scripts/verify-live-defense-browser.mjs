// Run after npm run build: CHROMIUM_BINARY=/path/to/chromium node scripts/verify-live-defense-browser.mjs
// Uses Chromium's fake camera, a synthetic microphone signal, and a local HTTP stub.
// OFFLINE_TTS=1 tests the bundled voice; OFFLINE_TTS=rate-limit tests its fallback after remote HTTP 429.
// OFFLINE_TTS=slow races delayed audio against the standby engine; server-only also disables the standby worker.
// OFFLINE_TTS=leave closes the room before either voice has started.
// No real credentials, student material, or speech provider is required.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { setTimeout as wait } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const profile = await mkdtemp(path.join(tmpdir(), 'practice-browser-'))
const user = { id: 17, username: 'test-student', real_name: '模拟学生', role: 'STUDENT' }
const calls = []
const offline = process.env.OFFLINE_TTS === '1'
const rateLimited = process.env.OFFLINE_TTS === 'rate-limit'
const serverOnly = process.env.OFFLINE_TTS === 'server-only'
const leaveEarly = process.env.OFFLINE_TTS === 'leave'
const slow = process.env.OFFLINE_TTS === 'slow'
const lateServer = serverOnly || leaveEarly || slow
const deviceScript = `
    localStorage.setItem('user_info', ${JSON.stringify(JSON.stringify(user))});
    localStorage.setItem('access_token', 'fake.' + btoa(JSON.stringify({ sub: '17', exp: 4000000000 })) + '.fake');
    // Browser-provided cloud speech must never receive private question text.
    Object.defineProperty(window.speechSynthesis, 'getVoices', { value: () => [{ lang: 'zh-CN', localService: false }] });
    Object.defineProperty(window.speechSynthesis, 'speak', { value: () => { window.practiceLeakedToCloudVoice = true; } });
    window.questionAudio = [];
    const play = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
      const record = { start: performance.now(), seconds: this.buffer.duration };
      window.questionAudio.push(record);
      this.addEventListener('ended', () => { record.end = performance.now(); }, { once:true });
      return play.apply(this, args);
    };
    if (${serverOnly}) {
      const OriginalWorker = window.Worker;
      window.Worker = class extends OriginalWorker {
        postMessage(...args) {
          if (typeof args[0] === 'string') { this.dispatchEvent(new ErrorEvent('error')); return; }
          super.postMessage(...args);
        }
      };
    }
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      const stream = await original({ ...constraints, audio: false });
      const context = new AudioContext();
      await context.resume();
      const output = context.createMediaStreamDestination();
      const tone = context.createOscillator();
      const volume = context.createGain();
      volume.gain.value = 0;
      tone.connect(volume).connect(output);
      tone.start();
      stream.addTrack(output.stream.getAudioTracks()[0]);
      window.practiceTestDevices = stream;
      window.practiceTestSpeak = () => {
        volume.gain.setValueAtTime(0.2, context.currentTime);
        volume.gain.setValueAtTime(0, context.currentTime + 0.6);
      };
      return stream;
    };
  `
const opener = '你这个项目主要解决了什么问题？'
const followup = '权限变了以后，原先缓存的权限怎么失效？'
const summary = '模拟报告已经生成。'
let reportReads = 0
let allowReportFailure = false
let reportRequeued = false
let finished = false
let browser
let socket

function wave() {
  const pcm = Buffer.alloc(16000 * 2 / 3 | 0)
  for (let i = 0; i < pcm.length / 2; i++) pcm.writeInt16LE(Math.round(1800 * Math.sin(i * Math.PI / 18)), i * 2)
  const hdr = Buffer.alloc(44)
  hdr.write('RIFF'); hdr.writeUInt32LE(36 + pcm.length, 4); hdr.write('WAVEfmt ', 8)
  hdr.writeUInt32LE(16, 16); hdr.writeUInt16LE(1, 20); hdr.writeUInt16LE(1, 22)
  hdr.writeUInt32LE(16000, 24); hdr.writeUInt32LE(32000, 28); hdr.writeUInt16LE(2, 32); hdr.writeUInt16LE(16, 34)
  hdr.write('data', 36); hdr.writeUInt32LE(pcm.length, 40)
  return Buffer.concat([hdr, pcm])
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')
  try {
    if (url.pathname.startsWith('/api/v1/')) {
      const endpoint = url.pathname.slice('/api/v1'.length)
      const ok = data => res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ code: 0, data }))
      const body = []
      for await (const chunk of req) body.push(chunk)
      calls.push({ endpoint, body: Buffer.concat(body).toString() })
      if (endpoint === '/auth/profile') return ok(user)
      if (endpoint === '/ai/documents') return ok({ total: 1, items: [{ practice_eligible: true }] })
      if (endpoint === '/ai/defense/sessions') return ok(finished ? [{ id: 42, status: 'FINISHED', question_count: 8, answered_count: 1 }] : [])
      if (endpoint === '/ai/defense/speech-capabilities') return ok({ speech: !offline, transcription: true, max_recording_seconds: 90 })
      if (endpoint === '/ai/defense/live/start') {
        await wait(900)
        return ok({ session_id: 42, finished: false, turn_no: 1, question: opener })
      }
      if (endpoint.endsWith('/speech')) {
        if (rateLimited) return res.writeHead(429, { 'Retry-After': '60' }).end()
        if (lateServer) await wait(slow ? 8000 : 3500)
        return res.writeHead(200, { 'Content-Type': 'audio/wav' }).end(wave())
      }
      if (endpoint.endsWith('/transcription')) {
        assert.match(Buffer.concat(body).toString(), /answer\.wav.*?RIFF/s)
        return ok({ text: endpoint.includes('/turns/1/') ? '我用 Redis 缓存权限。' : '权限更新后，我会删除对应的缓存。' })
      }
      if (endpoint === '/ai/defense/sessions/42/live/advance') {
        const data = JSON.parse(Buffer.concat(body).toString())
        if (data.turn_no === 1) {
          assert.equal(data.answer, '我用 Redis 缓存权限。')
          return ok({ finished: false, turn_no: 2, question: followup, is_follow_up: true })
        }
        assert.equal(data.turn_no, 2)
        assert.equal(data.answer, '权限更新后，我会删除对应的缓存。')
        finished = true
        return ok({ finished: true })
      }
      if (endpoint === '/ai/defense/sessions/42/report') {
        reportReads++
        if (!allowReportFailure) return ok({ report_status: 'pending', session_id: 42 })
        if (!reportRequeued) return ok({ report_status: 'failed', session_id: 42 })
        return ok({ report_status: 'ready', score_basis: 'main_questions', overall_score: 83,
          summary, turns: [], dimension_scores: {}, strengths: [], weaknesses: [], suggestions: [], risky_questions: [] })
      }
      if (endpoint === '/ai/defense/sessions/42/report/retry') {
        reportRequeued = true
        return ok({})
      }
      return ok({ items: [], total: 0 })
    }
    const file = url.pathname.startsWith('/student/') || url.pathname === '/'
      ? path.join(dist, 'index.html') : path.join(dist, url.pathname)
    assert.ok(file.startsWith(dist))
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm' }
    const contents = await readFile(file)
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' })
      .end(file.endsWith('index.html') ? contents.toString().replace('<head>', '<head><script>' + deviceScript + '</script>') : contents)
  } catch (error) {
    if (!res.headersSent) res.writeHead(500)
    res.end(String(error))
  }
})

async function until(check, description, timeout = 15000) {
  const end = Date.now() + timeout
  do {
    const value = await check()
    if (value) return value
    await wait(35)
  } while (Date.now() < end)
  throw new Error('Timed out: ' + description)
}

try {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  let browserErrors = ''
  browser = spawn(process.env.CHROMIUM_BINARY || 'chromium', [
    '--headless', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-zygote',
    '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    '--autoplay-policy=no-user-gesture-required', '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] })
  browser.stderr.setEncoding('utf8').on('data', data => { browserErrors += data })
  let spawnError
  browser.on('error', error => { spawnError = error })
  const cdpPort = await until(async () => {
    if (spawnError) throw spawnError
    if (browser.exitCode !== null) throw new Error(browserErrors)
    return (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8').catch(() => '')).split('\n')[0]
  }, 'Chromium debugging port')
  const pages = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json()
  socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  let id = 0
  function cdp(method, params = {}) {
    return new Promise((resolve, reject) => {
      const requestId = ++id
      const listener = ({ data }) => {
        const reply = JSON.parse(data)
        if (reply.id !== requestId) return
        socket.removeEventListener('message', listener)
        if (reply.error) reject(new Error(reply.error.message))
        else resolve(reply.result)
      }
      socket.addEventListener('message', listener)
      socket.send(JSON.stringify({ id: requestId, method, params }))
    })
  }
  async function evaluate(expression) {
    const response = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true })
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result.value
  }
  await cdp('Page.navigate', { url: `http://127.0.0.1:${port}/student/ai-defense?session=404` })
  const click = label => evaluate(`[...document.querySelectorAll('button')].find(x =>
    (x.textContent.trim() === ${JSON.stringify(label)} || x.getAttribute('aria-label') === ${JSON.stringify(label)}) && !x.disabled && getComputedStyle(x).visibility === 'visible')?.click() ?? true`)
  await until(() => evaluate('document.body?.innerText.includes("这场练习不可用")'), 'missing deep-linked session')
  assert.ok(await evaluate('!document.body.innerText.includes("开始视频答辩")'))
  await click('返回练习列表')
  await until(() => evaluate('document.body?.innerText.includes("开始视频答辩")'), 'student defense lobby')
  await click('开始视频答辩')
  await until(() => evaluate("Boolean(document.querySelector('dialog[open] video')?.srcObject)"), 'camera self-view')
  assert.equal(await evaluate("document.querySelector('.oral-stage').dataset.phase"), 'waiting')
  const visible = () => evaluate("document.querySelector('dialog[open]')?.innerText")
  const phase = () => evaluate("document.querySelector('.oral-stage')?.dataset.phase")
  const checkRoom = async () => {
    assert.doesNotMatch(await visible(), new RegExp(`${opener}|${followup}|Redis`))
    assert.equal(await evaluate("!!document.querySelector('dialog textarea, dialog input')"), false)
  }
  if (leaveEarly) {
    await until(() => calls.some(c => c.endpoint.endsWith('/speech')), 'pending server speech')
    await click('离开答辩')
    await wait(3800)
    assert.ok(await evaluate('window.practiceTestDevices.getTracks().every(track => track.readyState === "ended")'))
    assert.equal(await evaluate('window.questionAudio.length'), 0, 'no stale server or standby audio after exit')
    assert.ok(!calls.some(c => c.endpoint.endsWith('/transcription')))
    console.log('PASS — leaving while both voices are pending cancels playback and releases devices')
  } else {
    await verifyInterview()
  }

  async function verifyInterview() {
    await until(async () => await phase() === 'speaking', 'opener playback')
    await checkRoom()
    await until(async () => await phase() === 'listening', 'automatic recording')
    await evaluate('window.practiceTestSpeak()')
    await until(async () => await phase() === 'speaking', 'answer-aware follow-up playback')
    await checkRoom()
    await until(async () => await phase() === 'listening', 'automatic recording of follow-up')
    assert.equal(await evaluate('window.questionAudio.length'), 2, 'each question plays once even if a late response arrives')
    await evaluate('window.practiceTestSpeak()')
    await until(() => evaluate("document.querySelector('dialog [aria-label=\"答辩已结束\"]') !== null"), 'finish while no report has been generated')
    assert.equal(reportReads, 0)
    assert.ok(await evaluate('window.practiceTestDevices.getTracks().every(track => track.readyState === "ended")'))
    assert.equal(calls.filter(x => x.endpoint === '/ai/defense/live/start').length, 1)
    assert.equal(calls.filter(x => x.endpoint.endsWith('/live/advance')).length, 2)
    assert.equal(calls.filter(x => x.endpoint.endsWith('/speech')).length, offline ? 0 : rateLimited || slow ? 1 : 2)
    const playback = await evaluate('window.questionAudio.slice(0,2)')
    // Both test questions are single-phrase utterances. The completion announcement is separate.
    assert.ok(playback[0].end <= playback[1].start, 'one player at a time')
    for (const entry of playback)
      assert.equal(Math.abs(entry.seconds - 1 / 3) < .01, !(offline || rateLimited || slow),
        'a fast server or a working server with an unavailable standby should use the server WAV')
    assert.ok(await evaluate('!window.practiceLeakedToCloudVoice'))
    assert.equal(calls.filter(x => /generate$|\/answer$|\/next$/.test(x.endpoint)).length, 0)
    assert.deepEqual(await evaluate("performance.getEntriesByType('measure').filter(e => e.name.startsWith('practice:')).map(e => e.name)"),
      ['practice:devices-ready-to-question-audio-start', 'practice:endpoint-to-question-audio-start'])
    await click('查看练习报告')
    await until(() => evaluate('document.body?.innerText.includes("正在后台评阅")'), 'pending report UI')
    allowReportFailure = true
    await until(() => evaluate('document.body?.innerText.includes("后台评阅没有完成")'), 'failed report UI')
    await click('重试生成报告')
    await until(() => evaluate(`document.body?.innerText.includes(${JSON.stringify(summary)})`), 'ready report UI')
    assert.equal(calls.filter(x => x.endpoint.endsWith('/report/retry')).length, 1)
    assert.ok(await evaluate('document.body.innerText.includes("只按主问题计分")'))
    console.log('PASS — camera, two voice-only turns, timing, released devices, pending/failed/ready report, explicit retry')
  }
} finally {
  socket?.close()
  if (browser && browser.exitCode === null && browser.pid) {
    browser.kill()
    await new Promise(resolve => browser.once('exit', resolve))
  }
  server.closeAllConnections()
  server.close()
  await rm(profile, { recursive: true, force: true })
}
