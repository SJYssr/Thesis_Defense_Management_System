<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ObservationSignal } from '@/api/practiceObservations'
import { FaceReminder } from '@/utils/practiceFaceReminder'

const props = defineProps<{ sessionId: number; active: boolean; video: HTMLVideoElement | null }>()
const emit = defineEmits<{ signal: [kind: ObservationSignal] }>()
const visible = ref(!document.hidden)
const detectorFailed = ref(false)
const reminder = new FaceReminder()

function visibilityChanged() {
  if (document.hidden && visible.value && props.active) {
    emit('signal', 'TAB_HIDDEN')
  }
  visible.value = !document.hidden
}

watch(() => props.sessionId, () => { reminder.reset(); emit('signal', 'ENABLED') }, { immediate: true })
watch([visible, () => props.active, () => props.video], ([isVisible, active, video], _, cleanup) => {
  detectorFailed.value = false
  reminder.pause()
  if (!isVisible || !active || !video) return
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  let worker: Worker | null = null
  let timer: ReturnType<typeof setInterval>
  let deadline: ReturnType<typeof setTimeout>
  let previousTime = -1
  let awaitingFrame = false
  let live = true
  let frameId = 0

  function stop() {
    live = false
    clearInterval(timer)
    clearTimeout(deadline)
    worker?.terminate()
    worker = null
    canvas.width = canvas.height = 0
  }
  function failed() {
    stop()
    detectorFailed.value = true
  }
  cleanup(stop)

  function sample() {
    if (!video || !ctx || !live || awaitingFrame) return
    const tracks = (video.srcObject as MediaStream | null)?.getVideoTracks()
    if (document.hidden || !props.active || props.video !== video || video.paused || video.readyState < 2 ||
        !tracks?.some(t => t.readyState === 'live' && t.enabled && !t.muted) ||
        !video.videoWidth || !video.videoHeight || video.currentTime === previousTime) {
      reminder.pause()
      return
    }
    previousTime = video.currentTime
    // Keep the full field of view on portrait and landscape cameras.
    const scale = Math.min(1, 320 / Math.max(video.videoWidth, video.videoHeight))
    const width = Math.round(video.videoWidth * scale)
    const height = Math.round(video.videoHeight * scale)
    if (Math.min(width, height) < 40) { reminder.pause(); return }
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height }
    try {
      ctx.drawImage(video, 0, 0, width, height)
      const pixels = ctx.getImageData(0, 0, width, height).data.buffer
      awaitingFrame = true
      worker!.postMessage({ frameId: ++frameId, pixels, width, height }, [pixels])
      deadline = setTimeout(failed, 8000)
    } catch { failed() }
  }

  try {
    if (!ctx) { failed(); return }
    worker = new Worker(import.meta.env.BASE_URL + 'practice-detector/worker.js')
    deadline = setTimeout(failed, 15000)
    worker.onerror = failed
    worker.onmessage = ({ data }) => {
      if (!live) return
      clearTimeout(deadline)
      if (data.failed) { failed(); return }
      if (data.ready) { timer = setInterval(sample, 700); sample(); return }
      awaitingFrame = false
      if (!props.active || document.hidden || props.video !== video || data.frameId !== frameId) return
      if (data.count === null) { reminder.pause(); return }
      const signal = reminder.sample(data.count, performance.now())
      if (signal) emit('signal', signal)
    }
  } catch { failed() }
})

onMounted(() => document.addEventListener('visibilitychange', visibilityChanged))
onBeforeUnmount(() => document.removeEventListener('visibilitychange', visibilityChanged))
</script>

<template>
  <span v-if="detectorFailed" role="status" aria-label="环境检测暂不可用" />
</template>
