<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ObservationSignal } from '@/api/practiceObservations'
import { FaceReminder } from '@/utils/practiceFaceReminder'

const props = defineProps<{ sessionId: number; active: boolean; video: HTMLVideoElement | null }>()
const emit = defineEmits<{ signal: [kind: ObservationSignal]; requestCamera: []; releaseCamera: [] }>()
const canRequestCamera = !!navigator.mediaDevices?.getUserMedia
const enabled = ref(false)
const visible = ref(!document.hidden)
const status = ref('')
const lastNotice = ref('')
const retry = ref(0)
const detectorFailed = ref(false)

const reminder = new FaceReminder()
let requestedCamera = false

function disable() {
  enabled.value = false
  reminder.reset()
  lastNotice.value = ''
  if (requestedCamera) emit('releaseCamera')
  requestedCamera = false
}

function toggle() {
  if (enabled.value) { disable(); return }
  enabled.value = true
  lastNotice.value = ''
  emit('signal', 'ENABLED')
  requestedCamera = !props.video && canRequestCamera
  if (requestedCamera) emit('requestCamera')
}

function visibilityChanged() {
  if (document.hidden && visible.value && enabled.value && props.active) {
    emit('signal', 'TAB_HIDDEN')
    lastNotice.value = '本页刚刚进入后台，已记录 1 次。'
  }
  visible.value = !document.hidden
}

watch(() => props.sessionId, disable)
watch([enabled, visible, () => props.active, () => props.video, retry], ([on, isVisible, active, video], _, cleanup) => {
  detectorFailed.value = false
  reminder.pause()
  if (!on) return
  if (!isVisible || !active) { status.value = '镜头检测已暂停。'; return }
  if (!video) {
    status.value = canRequestCamera
      ? '等待可用的摄像头画面。摄像头关闭时只记录页面切出。'
      : '当前环境无法采集摄像头画面，只记录页面切出。'
    return
  }

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
    status.value = '镜头检测暂不可用，只记录页面切出。'
  }
  cleanup(stop)

  function sample() {
    if (!video || !ctx || !live || awaitingFrame) return
    const tracks = (video.srcObject as MediaStream | null)?.getVideoTracks()
    if (document.hidden || !props.active || props.video !== video || video.paused || video.readyState < 2 ||
        !tracks?.some(t => t.readyState === 'live' && t.enabled && !t.muted) ||
        !video.videoWidth || !video.videoHeight || video.currentTime === previousTime) {
      reminder.pause()
      status.value = '等待新的有效视频帧。'
      return
    }
    previousTime = video.currentTime
    // Keep the full field of view on portrait and landscape cameras.
    const scale = Math.min(1, 320 / Math.max(video.videoWidth, video.videoHeight))
    const width = Math.round(video.videoWidth * scale)
    const height = Math.round(video.videoHeight * scale)
    if (Math.min(width, height) < 40) { reminder.pause(); status.value = '视频分辨率过低，已暂停计数。'; return }
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
    status.value = '正在加载本机镜头检测…'
    deadline = setTimeout(failed, 15000)
    worker.onerror = failed
    worker.onmessage = ({ data }) => {
      if (!live) return
      clearTimeout(deadline)
      if (data.failed) { failed(); return }
      if (data.ready) { timer = setInterval(sample, 700); sample(); return }
      awaitingFrame = false
      if (!enabled.value || !props.active || document.hidden || props.video !== video || data.frameId !== frameId) return
      if (data.count === null) { reminder.pause(); status.value = '画面过暗或过亮，已暂停计数。'; return }
      const signal = reminder.sample(data.count, performance.now())
      status.value = data.count === 1 ? '镜头检测中，当前识别到一张正脸。' : '镜头检测中，正在核对连续画面。'
      if (signal) {
        emit('signal', signal)
        lastNotice.value = signal === 'NO_FACE'
          ? '连续 6 秒未检测到清晰正脸，可检查光线和镜头角度。'
          : '连续 4 秒检测到多张人脸，可检查画面中的其他人或背景照片。'
      }
    }
  } catch { failed() }
})

onMounted(() => document.addEventListener('visibilitychange', visibilityChanged))
onBeforeUnmount(() => document.removeEventListener('visibilitychange', visibilityChanged))
</script>

<template>
  <div class="practice-observation">
    <div class="observation-control">
      <el-button size="small" :aria-pressed="enabled" :disabled="!active && !enabled" @click="toggle">
        {{ enabled ? '关闭环境提醒' : '开启环境提醒' }}
      </el-button>
      <el-button v-if="enabled && detectorFailed && active" link type="primary" @click="retry++">重新加载检测</el-button>
    </div>
    <p>自愿开启。本机检测正脸数量及页面切出，只保存提醒类型与次数，供本人复盘，不计分。镜头画面不上传。</p>
    <template v-if="enabled">
      <p>{{ status }}</p>
      <p v-if="lastNotice" class="observation-notice" role="status">最近提醒：{{ lastNotice }}</p>
    </template>
  </div>
</template>

<style scoped>
.practice-observation { margin-top: 10px; }
.observation-control { display: flex; align-items: center; gap: 8px; }
.practice-observation p { margin: 5px 0 0; font-size: 12px; color: #58687c; line-height: 1.55; }
.practice-observation .observation-notice { color: #85560d; }
</style>
