// Classic worker so the MIT runtime stays isolated from the application window.
importScripts('./pico.js')

async function startDetector() {
  const response = await fetch('./facefinder.bin')
  if (!response.ok) throw new Error('模型未加载')
  const bytes = new Int8Array(await response.arrayBuffer())
  const header = new DataView(bytes.buffer)
  if (bytes.length !== 239632 || header.getInt32(8, true) !== 6 || header.getInt32(12, true) !== 468) {
    throw new Error('模型不完整')
  }
  const classify = pico.unpack_cascade(bytes)
  self.onmessage = ({ data: { pixels, width, height, frameId } }) => {
    const rgba = new Uint8ClampedArray(pixels)
    const gray = new Uint8Array(width * height)
    let usable = 0
    for (let i = 0; i < gray.length; i++) {
      const p = i * 4
      const light = (2 * rgba[p] + 7 * rgba[p + 1] + rgba[p + 2]) / 10
      gray[i] = light
      if (light > 20 && light < 242) usable++
    }
    if (usable < gray.length * 0.10) {
      self.postMessage({ frameId, count: null })
      return
    }
    const boxes = pico.run_cascade({ pixels: gray, nrows: height, ncols: width, ldim: width }, classify,
      { minsize: 28, maxsize: Math.min(width, height), scalefactor: 1.12, shiftfactor: 0.1 })
    const count = pico.cluster_detections(boxes, 0.2).filter(box => box[3] > 5).length
    self.postMessage({ frameId, count })
  }
  self.postMessage({ ready: true })
}

startDetector().catch(() => self.postMessage({ failed: true }))
