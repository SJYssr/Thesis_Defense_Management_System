<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ question: string; canDictate: boolean }>()
const emit = defineEmits<{ transcript: [text: string] }>()

// Web Speech 转写接口在部分浏览器中仅带 webkit 前缀。
interface Dictation {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Dictation
  webkitSpeechRecognition?: new () => Dictation
}

const synth = window.speechSynthesis
const speechWindow = window as SpeechWindow
const Recognizer = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
const canUseCamera = Boolean(navigator.mediaDevices?.getUserMedia)
const voices = ref<SpeechSynthesisVoice[]>([])
const voice = computed(() => voices.value.find(v => v.localService && /^zh/i.test(v.lang)))
const speaking = ref(false)
const dictating = ref(false)
const cameraPending = ref(false)
const cameraOn = ref(false)
const camera = ref<HTMLVideoElement | null>(null)
const message = ref('')
let speech: SpeechSynthesisUtterance | null = null
let recognition: Dictation | null = null
let stream: MediaStream | null = null
let cameraRequest = 0

function loadVoices() { voices.value = synth?.getVoices() || [] }

function stopSpeech() {
  if (!speech) return
  speech.onend = null
  speech.onerror = null
  speech = null
  speaking.value = false
  synth.cancel()
}

function readQuestion() {
  if (speaking.value) { stopSpeech(); return }
  if (!voice.value || !props.question) return
  abortDictation()
  message.value = ''
  speech = new SpeechSynthesisUtterance(props.question)
  speech.lang = voice.value.lang
  speech.voice = voice.value
  speech.onend = () => { speaking.value = false; speech = null }
  speech.onerror = () => {
    speaking.value = false
    speech = null
    message.value = '朗读未完成，可以重试或阅读上方题目。'
  }
  speaking.value = true
  synth.speak(speech)
}

function abortDictation() {
  if (!recognition) return
  const current = recognition
  recognition = null
  current.onresult = current.onerror = current.onend = null
  current.abort()
  dictating.value = false
}

function toggleDictation() {
  if (!Recognizer || !props.canDictate) return
  if (recognition) { recognition.stop(); return }
  stopSpeech()
  message.value = ''
  const current = new Recognizer()
  recognition = current
  current.lang = 'zh-CN'
  current.continuous = true
  current.interimResults = false
  current.onresult = event => {
    if (!props.canDictate || recognition !== current) return
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i]
      if (result?.isFinal && result[0].transcript) emit('transcript', result[0].transcript)
    }
  }
  current.onerror = event => {
    if (event.error === 'aborted') return
    message.value = event.error === 'not-allowed'
      ? '麦克风权限未开启，可继续输入文字。'
      : '语音转写未完成，可继续输入文字或重新开启。'
  }
  current.onend = () => {
    if (recognition === current) { recognition = null; dictating.value = false }
  }
  try {
    current.start()
    dictating.value = true
  } catch {
    abortDictation()
    message.value = '无法启动麦克风，可继续输入文字。'
  }
}

function stopCamera() {
  cameraRequest++
  cameraPending.value = false
  cameraOn.value = false
  stream?.getTracks().forEach(track => { track.onended = null; track.stop() })
  stream = null
  if (camera.value) camera.value.srcObject = null
}

async function toggleCamera() {
  if (cameraOn.value || cameraPending.value) { stopCamera(); return }
  const request = ++cameraRequest
  cameraPending.value = true
  message.value = ''
  try {
    const candidate = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
    if (cameraRequest !== request) { candidate.getTracks().forEach(t => t.stop()); return }
    stream = candidate
    candidate.getVideoTracks().forEach(t => { t.onended = stopCamera })
    cameraOn.value = true
    await nextTick()
    if (camera.value && cameraRequest === request) {
      camera.value.srcObject = candidate
      await camera.value.play()
    }
  } catch (err) {
    if (cameraRequest !== request) return
    stopCamera()
    message.value = err instanceof DOMException && err.name === 'NotAllowedError'
      ? '未获得摄像头权限，你可以直接作答。'
      : '未能打开摄像头，请检查设备或直接作答。'
  } finally {
    if (cameraRequest === request) cameraPending.value = false
  }
}

function stopDevices() { stopSpeech(); abortDictation(); stopCamera() }
function onVisibility() { if (document.hidden) stopDevices() }
watch(() => props.question, () => { stopSpeech(); abortDictation(); message.value = '' })
watch(() => props.canDictate, allowed => { if (!allowed) abortDictation() })
onMounted(() => {
  loadVoices()
  synth?.addEventListener('voiceschanged', loadVoices)
  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('pagehide', stopDevices)
})
onBeforeUnmount(() => {
  stopDevices()
  synth?.removeEventListener('voiceschanged', loadVoices)
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('pagehide', stopDevices)
})
</script>

<template>
  <div class="practice-media">
    <div class="tools">
      <el-button size="small" :disabled="!voice" :aria-pressed="speaking" @click="readQuestion">{{ speaking ? '停止朗读' : '朗读题目' }}</el-button>
      <el-button v-if="canDictate" size="small" :disabled="!Recognizer" :aria-pressed="dictating" @click="toggleDictation">{{ dictating ? '停止转写' : '语音输入' }}</el-button>
      <el-button size="small" :disabled="!canUseCamera" :aria-pressed="cameraOn" @click="toggleCamera">{{ cameraPending ? '取消开启' : cameraOn ? '关闭摄像头' : '预览摄像头' }}</el-button>
    </div>
    <p v-if="!voice">设备未提供本地中文朗读声音。</p>
    <p v-if="canDictate && Recognizer">语音输入使用浏览器转写服务，音频可能由服务商处理。转写文字可编辑，点击提交后才用于评价。</p>
    <p v-else-if="canDictate">当前浏览器未提供语音转写，可在下方输入回答。</p>
    <p v-if="!canUseCamera">摄像头需要支持视频采集的浏览器及 HTTPS 或本机地址。</p>
    <p v-else-if="cameraPending" role="status">等待摄像头授权…</p>
    <figure v-if="cameraOn">
      <video ref="camera" autoplay muted playsinline aria-label="本机摄像头预览" />
      <figcaption>本机镜头预览，仅供练习仪态。画面不录制、不上传。</figcaption>
    </figure>
    <p v-if="message" role="status" class="device-message">{{ message }}</p>
  </div>
</template>

<style scoped>
.practice-media { margin: 0 0 16px; }
.tools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.tools .el-button { margin: 0; }
.practice-media p, figcaption { font-size: 12px; color: #58687c; margin: 6px 0 0; line-height: 1.55; }
.practice-media .device-message { color: #85560d; }
figure { margin: 10px 0 0; }
video { width: min(100%, 320px); max-height: 200px; border-radius: 8px; background: #182736; transform: scaleX(-1); object-fit: contain; }
</style>
