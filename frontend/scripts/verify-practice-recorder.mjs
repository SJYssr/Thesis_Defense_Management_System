// Exercises the actual audio worklet without requiring a physical microphone.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const worklet = await readFile(new URL('../public/practice-audio/recorder.js', import.meta.url), 'utf8')
for (const sampleRate of [16000, 44100, 48000]) {
  let Recorder
  const frames = []
  const scope = {
    sampleRate, AudioWorkletProcessor: class {
      port = { postMessage: pcm => frames.push(pcm) }
    },
    registerProcessor: (_, type) => { Recorder = type },
  }
  vm.runInNewContext(worklet, scope)
  const capture = (maxSeconds = 1) => new Recorder({ processorOptions: { maxSeconds } })
  const feed = (recorder, seconds, amplitude) => {
    const block = new Float32Array(128).fill(amplitude)
    for (let i = 0; i < seconds * sampleRate; i += block.length) recorder.process([[block]])
  }
  const full = capture()
  const signal = new Float32Array(128).fill(0.5)
  for (let i = 0; i < sampleRate + 128; i += signal.length) full.process([[signal]])
  assert.equal(frames.length, 1)
  assert.equal(frames[0].length, 16000)
  assert.ok(frames[0].every(value => value === 16384))

  const partial = capture()
  partial.process([[new Float32Array(sampleRate / 2).fill(-1)]])
  partial.port.onmessage({ data: 'finish' })
  partial.port.onmessage({ data: 'finish' })
  assert.equal(frames.length, 2)
  assert.equal(frames[1].length, 8000)
  assert.ok(frames[1].every(value => value === -32768))

  const spoken = capture(25)
  feed(spoken, 8, 0)
  feed(spoken, 0.6, 0.08)
  feed(spoken, 2, 0)
  assert.equal(frames.length, 2, 'a short thinking pause must not submit the answer')
  feed(spoken, 1, 0.15)
  feed(spoken, 3.2, 0)
  assert.equal(frames.length, 3, 'finished speech is submitted once after silence')
  assert.ok(frames[2].length < 8 * 16000, 'exclude thinking time before speech')
  assert.ok(frames[2].some(value => value > 4000), 'include speech after the pause')

  const quiet = capture(15)
  feed(quiet, 20, 0)
  feed(quiet, 0.025, 0.12)  // microphone transient
  feed(quiet, 21, 0)
  assert.equal(frames.length, 4)
  assert.equal(frames[3].length, 0, 'silence and brief transients cannot become an answer')
}
console.log('PASS — 16/44.1/48 kHz capture, 16 kHz PCM16, voice onset, thinking pauses, automatic endpoint and silence timeout')
