function pcmWave(samples: Int16Array): Blob {
  const header = new ArrayBuffer(44)
  const view = new DataView(header)
  const write = (at: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i))
  }
  write(0, 'RIFF')
  view.setUint32(4, 36 + samples.byteLength, true)
  write(8, 'WAVEfmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, 16000, true)
  view.setUint32(28, 32000, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  write(36, 'data')
  view.setUint32(40, samples.byteLength, true)
  return new Blob([header, new Int16Array(samples).buffer], { type: 'audio/wav' })
}

export async function recordAnswer(maxSeconds: number, signal: AbortSignal, ready: (finish: () => void) => void): Promise<Blob> {
  signal.throwIfAborted()
  const context = new AudioContext()
  const resumed = context.resume().catch(() => {})
  const base = new URL(import.meta.env.BASE_URL, window.location.origin)
  let stream: MediaStream | undefined
  let recorder: AudioWorkletNode | undefined
  let timeout: ReturnType<typeof setTimeout> | undefined
  const release = () => {
    stream?.getTracks().forEach(track => { track.onended = null; track.stop() })
    recorder?.disconnect()
    if (context.state !== 'closed') void context.close().catch(() => {})
  }
  signal.addEventListener('abort', release, { once: true })
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true }, video: false })
    signal.throwIfAborted()
    await context.audioWorklet.addModule(new URL('practice-audio/recorder.js', base))
    await resumed
    signal.throwIfAborted()
    if (context.state !== 'running') throw new Error('录音设备未启动，请重新开启语音作答。')
    const node = new AudioWorkletNode(context, 'practice-answer-recorder', {
      channelCount: 1, channelCountMode: 'explicit', processorOptions: { maxSeconds },
    })
    recorder = node
    context.createMediaStreamSource(stream).connect(node)
    node.connect(context.destination) // The processor's output is silent.
    const track = stream.getAudioTracks()[0]
    if (!track || track.readyState === 'ended') throw new Error('麦克风连接已中断。')
    return await new Promise<Blob>((resolve, reject) => {
      const aborted = () => reject(new DOMException('Aborted', 'AbortError'))
      signal.addEventListener('abort', aborted, { once: true })
      const finish = () => node.port.postMessage('finish')
      track.onended = () => reject(new Error('麦克风连接已中断。'))
      node.onprocessorerror = () => reject(new Error('录音处理失败。'))
      node.port.onmessage = ({ data }) => {
        signal.removeEventListener('abort', aborted)
        if (!(data instanceof Int16Array) || data.length < 1600) reject(new Error('录音太短，请重新作答。'))
        else resolve(pcmWave(data))
      }
      timeout = setTimeout(finish, maxSeconds * 1000)
      ready(finish)
    })
  } finally {
    clearTimeout(timeout)
    signal.removeEventListener('abort', release)
    release()
  }
}
