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
  const capture = () => new Recorder({ processorOptions: { maxSeconds: 1 } })
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
}
console.log('PASS — 16/44.1/48 kHz capture, timed and manual stop, mono 16 kHz PCM16 output')
