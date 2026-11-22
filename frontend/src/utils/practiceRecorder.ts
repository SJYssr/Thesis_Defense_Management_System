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

/** The room owns the MediaStream and unlocked AudioContext for its entire visit. */
export async function recordAnswer(stream: MediaStream, context: AudioContext, maxSeconds: number,
                                   signal: AbortSignal, ready: () => void): Promise<Blob | null> {
  signal.throwIfAborted()
  if (context.state !== 'running') throw new Error('录音设备尚未就绪')
  const base = new URL(import.meta.env.BASE_URL, location.origin)
  await context.audioWorklet.addModule(new URL('practice-audio/recorder.js', base))
  signal.throwIfAborted()
  const track = stream.getAudioTracks()[0]
  if (!track || track.readyState !== 'live') throw new Error('麦克风连接已中断')
  const source = context.createMediaStreamSource(stream)
  const node = new AudioWorkletNode(context, 'practice-answer-recorder', {
    channelCount: 1, channelCountMode: 'explicit', processorOptions: { maxSeconds },
  })
  let watchdog: ReturnType<typeof setTimeout> | undefined
  let rejectCapture: (error: Error) => void
  const aborted = () => rejectCapture(new DOMException('Aborted', 'AbortError'))
  const lost = () => rejectCapture(new Error('麦克风连接已中断'))
  try {
    return await new Promise<Blob | null>((resolve, reject) => {
      rejectCapture = reject
      signal.addEventListener('abort', aborted, { once: true })
      track.addEventListener('ended', lost, { once: true })
      node.onprocessorerror = () => reject(new Error('录音处理失败'))
      node.port.onmessage = ({ data }) => {
        if (!(data instanceof Int16Array)) { reject(new Error('录音处理失败')); return }
        resolve(data.length ? pcmWave(data) : null)
      }
      watchdog = setTimeout(() => reject(new Error('录音设备超时')), (maxSeconds + 45) * 1000)
      source.connect(node)
      node.connect(context.destination) // The processor outputs silence.
      ready()
    })
  } finally {
    signal.removeEventListener('abort', aborted)
    track.removeEventListener('ended', lost)
    clearTimeout(watchdog)
    source.disconnect()
    node.disconnect()
    node.port.close()
  }
}
