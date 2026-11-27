// The offline engine runs in its own same-origin worker. Only PCM samples cross this boundary.
let audioContext: AudioContext | null = null
let activation: Promise<void> | undefined
let standbyWorker: Worker | undefined

export function localMandarinVoice(): SpeechSynthesisVoice | undefined {
  return window.speechSynthesis?.getVoices().find(v => v.localService && /^(zh|cmn)/i.test(v.lang))
}

function newOfflineWorker() {
  const base = new URL(import.meta.env.BASE_URL, window.location.origin)
  return new Worker(new URL('practice-voice/worker.js', base), { type: 'module' })
}

/** Call during the click that enables voice practice, before the question API returns. */
export function primePracticeVoice(): void {
  try {
    audioContext ??= new AudioContext()
    if (audioContext.state !== 'running') activation = audioContext.resume().catch(() => {})
    // Load the site's Mandarin voice while device permission and the first question are pending.
    if (!localMandarinVoice()) standbyWorker ??= newOfflineWorker()
  } catch { /* Native speech synthesis can still be used without Web Audio. */ }
}

export function releasePracticeVoice(): void {
  standbyWorker?.terminate()
  standbyWorker = undefined
  const current = audioContext
  audioContext = null
  activation = undefined
  if (current && current.state !== 'closed') void current.close().catch(() => {})
}

/** Split long utterances for native engines and keep generated PCM buffers small. */
export function speechPhrases(text: string): string[] {
  return (text.trim().match(/[^。！？；\n]{1,180}[。！？；\n]*|[。！？；\n]+/gu) ?? [])
    .filter(part => part.trim())
}

export async function activeContext(signal: AbortSignal): Promise<AudioContext> {
  signal.throwIfAborted()
  const context = audioContext
  if (context && context.state !== 'running' && activation) {
    // Calling resume() on the gesture starts asynchronously; autoplay policy may also leave it pending.
    await Promise.race([activation, new Promise<void>(resolve => setTimeout(resolve, 800))])
    signal.throwIfAborted()
  }
  if (!context || context.state !== 'running') throw new Error('activation')
  return context
}

async function playBuffer(context: AudioContext, buffer: AudioBuffer, signal: AbortSignal, onStart?: () => void): Promise<void> {
  signal.throwIfAborted()
  const source = context.createBufferSource()
  source.buffer = buffer
  source.connect(context.destination)
  let timer: ReturnType<typeof setTimeout> | undefined
  let cancel: (() => void) | undefined
  let started = false
  try {
    await new Promise<void>((resolve, reject) => {
      cancel = () => reject(signal.reason)
      signal.addEventListener('abort', cancel, { once: true })
      source.onended = () => resolve()
      const start = context.currentTime
      source.start(start)
      started = true
      timer = setTimeout(() => {
        // Sleep or an audio-device interruption can swallow ended. Wall time alone cannot prove playback.
        if (context.state === 'running' && context.currentTime >= start + buffer.duration) resolve()
        else {
          if (audioContext === context) releasePracticeVoice()
          reject(new Error('朗读已中断'))
        }
      }, buffer.duration * 1000 + 1500)
      onStart?.()
    })
  } finally {
    clearTimeout(timer)
    if (cancel) signal.removeEventListener('abort', cancel)
    source.onended = null
    if (started) source.stop()
    source.disconnect()
  }
}

/** Lease one worker per pending utterance so replies from an aborted turn cannot reach the next. */
async function offlineBuffer(context: AudioContext, phrase: string, signal: AbortSignal): Promise<AudioBuffer> {
  signal.throwIfAborted()
  const worker = standbyWorker ?? newOfflineWorker()
  standbyWorker = undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let cancel: (() => void) | undefined
  let reusable = false
  try {
    const audio = await new Promise<AudioBuffer>((resolve, reject) => {
      const fail = () => reject(new Error('offline voice unavailable'))
      cancel = () => reject(new DOMException('Aborted', 'AbortError'))
      signal.addEventListener('abort', cancel, { once: true })
      worker.onerror = worker.onmessageerror = fail
      worker.onmessage = ({ data }) => {
        if (!(data.samples instanceof Int16Array) || !data.samples.length || !data.sampleRate) { fail(); return }
        try {
          const buffer = context.createBuffer(1, data.samples.length, data.sampleRate)
          const samples = buffer.getChannelData(0)
          for (let i = 0; i < samples.length; i++) samples[i] = data.samples[i] / 32768
          resolve(buffer)
        } catch (error) { reject(error) }
      }
      timer = setTimeout(fail, 30000)
      worker.postMessage(phrase)
    })
    reusable = true
    return audio
  } finally {
    clearTimeout(timer)
    if (cancel) signal.removeEventListener('abort', cancel)
    worker.onmessage = worker.onerror = worker.onmessageerror = null
    if (reusable && audioContext === context && !standbyWorker) standbyWorker = worker
    else worker.terminate()
  }
}

/** Prefer a prompt server response. If it takes longer, prepare one offline phrase, then play only the winner. */
export async function speakWithServer(text: string, signal: AbortSignal, load: (signal: AbortSignal) => Promise<Blob>,
                                      onStart: () => void): Promise<'server' | 'offline'> {
  const context = await activeContext(signal)
  const phrases = speechPhrases(text)
  if (!phrases.length) throw new Error('没有可朗读的问题')
  const preparing = new AbortController()
  const cancel = () => preparing.abort()
  signal.throwIfAborted()
  signal.addEventListener('abort', cancel, { once: true })
  let timer: ReturnType<typeof setTimeout> | undefined
  let offline: Promise<AudioBuffer> | undefined
  const prepareLocal = () => offline ??= offlineBuffer(context, phrases[0], preparing.signal)
  const server = load(preparing.signal).then(async blob => {
    const buffer = await context.decodeAudioData(await blob.arrayBuffer())
    return { voice: 'server' as const, buffer }
  })
  let chosen: { voice: 'server' | 'offline'; buffer: AudioBuffer }
  try {
    // If the standby engine is unavailable, continue waiting for the configured voice.
    const alternate = new Promise<void>(resolve => { timer = setTimeout(resolve, 1200) })
      .then(prepareLocal).then(buffer => ({ voice: 'offline' as const, buffer })).catch(() => server)
    try { chosen = await Promise.race([server, alternate]) }
    catch (error) {
      signal.throwIfAborted()
      const status = (error as { response?: { status?: number } }).response?.status
      if (status && status >= 400 && status < 500 && status !== 429) throw error
      chosen = { voice: 'offline', buffer: await prepareLocal() }
    }
  } finally {
    clearTimeout(timer)
    preparing.abort()
    signal.removeEventListener('abort', cancel)
  }
  await playBuffer(context, chosen.buffer, signal, onStart)
  if (chosen.voice === 'offline') {
    for (const phrase of phrases.slice(1))
      await playBuffer(context, await offlineBuffer(context, phrase, signal), signal)
  }
  return chosen.voice
}

/** Wait for actual playback completion before the room starts capturing the answer. */
async function nativePhrase(phrase: string, voice: SpeechSynthesisVoice, signal: AbortSignal, onStart?: () => void) {
  signal.throwIfAborted()
  const synth = window.speechSynthesis
  const utterance = new SpeechSynthesisUtterance(phrase)
  utterance.voice = voice
  utterance.lang = voice.lang
  await new Promise<void>((resolve, reject) => {
    const settle = (error?: Error) => {
      clearTimeout(timer)
      signal.removeEventListener('abort', cancel)
      utterance.onend = utterance.onerror = utterance.onstart = null
      if (error) { synth.cancel(); reject(error) }
      else resolve()
    }
    const cancel = () => settle(new DOMException('Aborted', 'AbortError'))
    let timer = setTimeout(() => settle(new Error('未能启动朗读')), 1500)
    signal.addEventListener('abort', cancel, { once: true })
    utterance.onstart = () => {
      clearTimeout(timer)
      timer = setTimeout(() => settle(new Error('朗读已中断')), 90000)
      onStart?.()
    }
    utterance.onend = () => settle()
    utterance.onerror = () => settle(new Error('设备朗读失败'))
    try { synth.speak(utterance) } catch { settle(new Error('设备朗读失败')) }
  })
}

export async function speakLocally(text: string, signal: AbortSignal, onStart?: () => void): Promise<void> {
  const voice = localMandarinVoice()
  for (const phrase of speechPhrases(text)) {
    signal.throwIfAborted()
    if (voice && window.SpeechSynthesisUtterance) {
      let started = false
      try { await nativePhrase(phrase, voice, signal, () => { started = true; onStart?.() }); continue }
      catch (error) { signal.throwIfAborted(); if (started) throw error }
    }
    const context = await activeContext(signal)
    await playBuffer(context, await offlineBuffer(context, phrase, signal), signal, onStart)
  }
}
