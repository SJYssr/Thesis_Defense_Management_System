// The offline engine runs in its own same-origin worker. Only PCM samples cross this boundary.
let audioContext: AudioContext | null = null
let activation: Promise<void> | undefined

/** Call during the click that enables voice practice, before the question API returns. */
export function primePracticeVoice(): void {
  try {
    audioContext ??= new AudioContext()
    if (audioContext.state !== 'running') activation = audioContext.resume().catch(() => {})
  } catch { /* Native speech synthesis can still be used without Web Audio. */ }
}

export function releasePracticeVoice(): void {
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

export async function playQuestionAudio(blob: Blob, signal: AbortSignal, onStart: () => void): Promise<void> {
  const context = await activeContext(signal)
  const buffer = await context.decodeAudioData(await blob.arrayBuffer())
  signal.throwIfAborted()
  const source = context.createBufferSource()
  source.buffer = buffer
  source.connect(context.destination)
  await new Promise<void>((resolve, reject) => {
    const finish = () => {
      signal.removeEventListener('abort', cancel)
      source.onended = null
      source.disconnect()
      resolve()
    }
    const cancel = () => { source.stop(); finish() }
    signal.addEventListener('abort', cancel, { once: true })
    source.onended = finish
    try { source.start(); onStart() } catch (err) {
      signal.removeEventListener('abort', cancel)
      source.disconnect()
      reject(err)
    }
  })
}

export async function playOfflineSpeech(text: string, signal: AbortSignal, onStart?: () => void): Promise<void> {
  const context = await activeContext(signal)
  const phrases = speechPhrases(text)
  if (!phrases.length) return
  const base = new URL(import.meta.env.BASE_URL, window.location.origin)
  const worker = new Worker(new URL('practice-voice/worker.js', base), { type: 'module' })
  let source: AudioBufferSourceNode | null = null
  let timer: ReturnType<typeof setTimeout>
  let abort: (() => void) | undefined
  try {
    await new Promise<void>((resolve, reject) => {
      const fail = () => reject(new Error('offline voice unavailable'))
      abort = () => resolve()
      signal.addEventListener('abort', abort, { once: true })
      const next = () => {
        const phrase = phrases.shift()
        if (phrase === undefined) { resolve(); return }
        timer = setTimeout(fail, 30000)
        worker.postMessage(phrase)
      }
      worker.onerror = fail
      worker.onmessageerror = fail
      worker.onmessage = ({ data }) => {
        clearTimeout(timer)
        if (signal.aborted) return
        if (!(data.samples instanceof Int16Array) || !data.samples.length || !data.sampleRate) { fail(); return }
        try {
          const buffer = context.createBuffer(1, data.samples.length, data.sampleRate)
          const samples = buffer.getChannelData(0)
          for (let i = 0; i < samples.length; i++) samples[i] = data.samples[i] / 32768
          source = context.createBufferSource()
          source.buffer = buffer
          source.connect(context.destination)
          source.onended = () => { source?.disconnect(); source = null; next() }
          source.start()
          onStart?.()
        } catch { fail() }
      }
      next()
    })
  } finally {
    clearTimeout(timer!)
    if (abort) signal.removeEventListener('abort', abort)
    worker.terminate()
    const playing = source as AudioBufferSourceNode | null
    if (playing) { playing.onended = null; playing.stop(); playing.disconnect() }
  }
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
    let timer = setTimeout(() => settle(new Error('未能启动朗读')), 5000)
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
  const voice = window.speechSynthesis?.getVoices().find(v => v.localService && /^(zh|cmn)/i.test(v.lang))
  for (const phrase of speechPhrases(text)) {
    signal.throwIfAborted()
    if (voice && window.SpeechSynthesisUtterance) {
      try { await nativePhrase(phrase, voice, signal, onStart); continue } catch { signal.throwIfAborted() }
    }
    await playOfflineSpeech(phrase, signal, onStart)
  }
}
