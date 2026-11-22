// SPDX-License-Identifier: GPL-3.0-or-later
// Self-hosted eSpeak NG / Spellophone worker. See NOTICE.txt for corresponding source.
import { createEspeak } from './engine/browser.js'

const engine = createEspeak({
  dataUrl: new URL('./espeak-ng-data/', import.meta.url),
  wasmUrl: new URL('./wasm/espeak-ng.wasm', import.meta.url),
  languages: ['cmn'],
})

self.onmessage = async ({ data }) => {
  try {
    const speaker = await engine
    const { samples, sampleRate } = speaker.synthesize(data, { voice: 'cmn', rate: 165 })
    self.postMessage({ samples, sampleRate }, [samples.buffer])
  } catch {
    self.postMessage({ error: true })
  }
}
