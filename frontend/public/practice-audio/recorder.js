// Capture one mono answer on the audio thread, then downsample it to PCM16 at 16 kHz.
class AnswerRecorder extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const seconds = Math.max(1, Math.min(90, options.processorOptions.maxSeconds))
    this.input = new Float32Array(Math.floor(sampleRate * seconds))
    this.length = 0
    this.done = false
    this.port.onmessage = ({ data }) => { if (data === 'finish') this.finish() }
  }

  finish() {
    if (this.done) return
    this.done = true
    const ratio = sampleRate / 16000
    const pcm = new Int16Array(Math.floor(this.length / ratio))
    for (let i = 0; i < pcm.length; i++) {
      const start = Math.floor(i * ratio)
      const end = Math.min(this.length, Math.max(start + 1, Math.floor((i + 1) * ratio)))
      let sum = 0
      for (let j = start; j < end; j++) sum += this.input[j]
      const value = Math.max(-1, Math.min(1, sum / (end - start)))
      pcm[i] = Math.round(value * (value < 0 ? 32768 : 32767))
    }
    this.port.postMessage(pcm, [pcm.buffer])
  }

  process(inputs) {
    if (this.done) return false
    const mono = inputs[0]?.[0]
    if (mono) {
      const take = Math.min(mono.length, this.input.length - this.length)
      this.input.set(mono.subarray(0, take), this.length)
      this.length += take
      if (this.length === this.input.length) this.finish()
    }
    return !this.done
  }
}
registerProcessor('practice-answer-recorder', AnswerRecorder)
