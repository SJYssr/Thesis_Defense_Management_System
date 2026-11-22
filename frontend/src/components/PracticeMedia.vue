<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import request from '@/api/request'
import PracticeAnswerInput from '@/components/PracticeAnswerInput.vue'
import { playQuestionAudio, playOfflineSpeech, primePracticeVoice, releasePracticeVoice, speechPhrases } from '@/utils/practiceVoice'

const props = defineProps<{ question: string; sessionId: number; turnNo: number; turnKey: string; canDictate: boolean; autoRead: boolean }>()
const emit = defineEmits<{ transcript: [text: string]; submitTranscript: []; 'update:autoRead': [enabled: boolean] }>()

const synth = window.speechSynthesis
const answerInput = ref<InstanceType<typeof PracticeAnswerInput> | null>(null)
const canUseCamera = Boolean(navigator.mediaDevices?.getUserMedia)
const voices = ref<SpeechSynthesisVoice[]>([])
const voice = computed(() => {
  const local = voices.value.filter(v => v.localService)
  return local.find(v => /^(zh[-_](CN|Hans)|cmn)/i.test(v.lang))
    || local.find(v => /^zh(?:$|[-_](TW|SG|Hant))/i.test(v.lang))
})
const speaking = ref(false)
const voiceStatus = ref('')
let cachedQuestionAudio: Blob | null = null
const cameraPending = ref(false)
const cameraOn = ref(false)
const camera = ref<HTMLVideoElement | null>(null)
const message = ref('')
let speech: SpeechSynthesisUtterance | null = null
let playback: AbortController | null = null
let readTurn = ''
let stream: MediaStream | null = null
let cameraRequest = 0

function loadVoices() { voices.value = synth?.getVoices() || [] }

function stopSpeech() {
  playback?.abort()
  playback = null
  speaking.value = false
  if (voiceStatus.value === '正在生成题目语音…') voiceStatus.value = ''
  if (speech) {
    speech.onend = speech.onerror = null
    speech = null
    synth.cancel()
  }
}

async function readQuestion() {
  if (speaking.value) { stopSpeech(); return }
  if (!props.question || document.hidden) return
  readTurn = props.turnKey
  answerInput.value?.cancel()
  const attempt = new AbortController()
  playback = attempt
  message.value = ''
  speaking.value = true
  voiceStatus.value = '正在生成题目语音…'
  const complete = () => {
    if (playback === attempt) { playback = null; speaking.value = false; speech = null }
  }
  try {
    const options = { responseType: 'blob' as const, _silent: true, signal: attempt.signal, timeout: 70000 }
    const audio = cachedQuestionAudio || await request.get<Blob, Blob>(
      `/ai/defense/sessions/${props.sessionId}/turns/${props.turnNo}/speech`, options)
    attempt.signal.throwIfAborted()
    await playQuestionAudio(audio, attempt.signal, () => {
      cachedQuestionAudio = audio
      voiceStatus.value = '语音来源：自托管语音模型'
    })
    complete()
    return
  } catch {
    if (attempt.signal.aborted) return
  }
  const offline = async (text: string) => {
    try {
      await playOfflineSpeech(text, attempt.signal, () => { voiceStatus.value = '语音来源：本机离线普通话' })
    }
    catch (err) {
      if (attempt.signal.aborted) return
      voiceStatus.value = ''
      message.value = err instanceof Error && err.message === 'activation'
        ? '浏览器需要你先点击「朗读题目」开启声音。'
        : '离线语音未能播放，请检查声音设置后点击「朗读题目」重试。'
    } finally { complete() }
  }

  const native = voice.value
  const phrases = speechPhrases(props.question)
  if (!native || !window.SpeechSynthesisUtterance) { void offline(props.question); return }
  const next = () => {
    if (attempt.signal.aborted) return
    const phrase = phrases.shift()
    if (phrase === undefined) { complete(); return }
    speech = new SpeechSynthesisUtterance(phrase)
    speech.lang = native.lang
    speech.voice = native
    speech.onstart = () => { if (!attempt.signal.aborted) voiceStatus.value = '语音来源：设备中文语音' }
    speech.onend = next
    const fallback = () => {
      if (attempt.signal.aborted) return
      speech = null
      void offline(phrase + phrases.join(''))
    }
    speech.onerror = fallback
    try { synth.speak(speech) } catch { fallback() }
  }
  next()
}

function manualRead() {
  primePracticeVoice()
  readQuestion()
}

function changeAutoRead(enabled: boolean) {
  if (enabled) primePracticeVoice()
  emit('update:autoRead', enabled)
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

function requestCamera() {
  if (!cameraOn.value && !cameraPending.value) void toggleCamera()
}

function stopDevices() { stopSpeech(); answerInput.value?.cancel(); stopCamera() }
function onVisibility() { if (document.hidden) stopDevices() }
watch(() => props.turnKey, () => {
  stopSpeech(); answerInput.value?.cancel(); message.value = ''
  cachedQuestionAudio = null
  voiceStatus.value = ''
})
watch(() => props.canDictate, allowed => { if (!allowed) { answerInput.value?.cancel(); stopSpeech() } })
watch(() => props.autoRead, enabled => {
  if (!enabled) stopSpeech()
  else readTurn = speaking.value ? props.turnKey : ''
})
function autoReadIfReady() {
  if (props.autoRead && props.canDictate && props.turnKey !== readTurn && !document.hidden) readQuestion()
}
watch(() => [props.turnKey, props.autoRead, props.canDictate], autoReadIfReady, { flush: 'post' })
onMounted(() => {
  loadVoices()
  autoReadIfReady()
  synth?.addEventListener('voiceschanged', loadVoices)
  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('pagehide', stopDevices)
})
onBeforeUnmount(() => {
  stopDevices()
  releasePracticeVoice()
  synth?.removeEventListener('voiceschanged', loadVoices)
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('pagehide', stopDevices)
})
</script>

<template>
  <div class="practice-media">
    <div class="tools">
      <el-switch :model-value="autoRead" aria-label="连续语音提问" active-text="连续语音提问" @update:model-value="changeAutoRead($event === true)" />
      <el-button size="small" :aria-pressed="speaking" @click="manualRead">{{ speaking ? '停止朗读' : '朗读题目' }}</el-button>
      <el-button size="small" :disabled="!canUseCamera" :aria-pressed="cameraOn" @click="toggleCamera">{{ cameraPending ? '取消开启' : cameraOn ? '关闭摄像头' : '预览摄像头' }}</el-button>
    </div>
    <p v-if="voiceStatus" role="status">{{ voiceStatus }} <a v-if="voiceStatus === '语音来源：本机离线普通话'" href="/practice-voice/NOTICE.txt" target="_blank" rel="noopener">语音组件来源与许可</a></p>
    <PracticeAnswerInput ref="answerInput" :session-id="sessionId" :turn-no="turnNo" :can-dictate="canDictate"
                         @recording-start="stopSpeech" @transcript="emit('transcript', $event)"
                         @submit-transcript="emit('submitTranscript')" />
    <p v-if="!canUseCamera">摄像头需要支持视频采集的浏览器及 HTTPS 或本机地址。</p>
    <p v-else-if="cameraPending" role="status">等待摄像头授权…</p>
    <figure v-if="cameraOn">
      <video ref="camera" autoplay muted playsinline aria-label="本机摄像头预览" />
      <figcaption>本机镜头预览，仅供练习仪态。画面不录制、不上传。</figcaption>
    </figure>
    <slot name="observation" :video="camera" :request-camera="requestCamera" :release-camera="stopCamera" />
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
