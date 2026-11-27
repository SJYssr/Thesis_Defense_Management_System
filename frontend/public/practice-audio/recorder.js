// Capture speech on the audio thread. A quiet pause ends the answer only after voice onset.
class AnswerRecorder extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const seconds = Math.max(1, Math.min(90, options.processorOptions.maxSeconds))
    this.input = new Float32Array(Math.floor(sampleRate * seconds))
    this.length = 0
    this.waited = 0
    this.voiceSamples = 0
    this.quietSamples = 0
    this.noiseFloor = 0.002
    this.started = false
    this.done = false
    this.port.onmessage = ({ data }) => { if (data === 'finish') this.finish() }
  }

  finish(endedBySilence = false) {
    if (this.done) return
    this.done = true
    // Keep the whole pause when deciding whether the student has finished. Only the WAV
    // sent to ASR drops excess trailing silence, retaining half a second for quiet endings.
    if (endedBySilence) this.length -= this.quietSamples - Math.round(0.5 * sampleRate)
    const ratio = sampleRate / 16000
    const pcm = new Int16Array(this.started ? Math.floor(this.length / ratio) : 0)
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
    if (!mono?.length) return true
    let energy = 0
    for (let i = 0; i < mono.length; i++) energy += mono[i] * mono[i]
    const rms = Math.sqrt(energy / mono.length)
    const voiced = rms > Math.max(0.009, this.noiseFloor * 3.2)
    if (!this.started) {
      this.waited += mono.length
      if (!voiced) this.noiseFloor += (rms - this.noiseFloor) * 0.015
      this.voiceSamples = Math.max(0, this.voiceSamples + (voiced ? mono.length : -mono.length / 2))
      this.started = this.voiceSamples >= sampleRate * 0.15
      // Keep the beginning of the first word, but exclude long thinking time from the answer limit.
      if (!this.started && this.length >= sampleRate * 0.55) {
        const drop = Math.floor(sampleRate * 0.25)
        this.input.copyWithin(0, drop, this.length)
        this.length -= drop
      }
    } else {
      this.quietSamples = voiced ? 0 : this.quietSamples + mono.length
    }

    const take = Math.min(mono.length, this.input.length - this.length)
    this.input.set(mono.subarray(0, take), this.length)
    this.length += take
    const endedBySilence = this.quietSamples >= sampleRate * 3
    if (this.length === this.input.length || endedBySilence ||
        (!this.started && this.waited >= sampleRate * 40)) this.finish(endedBySilence)
    return !this.done
  }
}
registerProcessor('practice-answer-recorder', AnswerRecorder)
