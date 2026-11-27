<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CloseBold, Loading, Microphone, RefreshRight, Warning, Headset } from '@element-plus/icons-vue'
import request from '@/api/request'
import type { ApiEnvelope, PracticeSpeechCapabilities } from '@/types/api'
import type { ObservationSignal } from '@/api/practiceObservations'
import PracticeObservation from './PracticeObservation.vue'
import { activeContext, playQuestionAudio, primePracticeVoice, speakLocally } from '@/utils/practiceVoice'
import { recordAnswer } from '@/utils/practiceRecorder'

const props = defineProps<{
  stream: MediaStream; sessionId: number | null; turnNo: number | null; question: string
  active: boolean; parentError?: string; observe?: boolean; getCapabilities: () => Promise<PracticeSpeechCapabilities>
}>()
const emit = defineEmits<{
  answer: [text: string, endpointedAt: number]; audioStart: []; retry: []; leave: []; signal: [kind: ObservationSignal]
}>()
const video = ref<HTMLVideoElement | null>(null)
const videoReady = ref(false)
const videoFault = ref(false)
const stage = ref<'waiting' | 'speaking' | 'listening' | 'transcribing' | 'fault'>('waiting')
const parentGuidance = computed(() => {
  const error = props.parentError || ''
  if (!error) return ''
  if (/材料.{0,8}(不足|不够|已更新|已移除)|没有可核对|缺少可核对|补充材料/.test(error))
    return '当前材料无法支持这次练习。请退出答辩，检查或补充自己的材料后重新开始。'
  if (/繁忙|频繁|限流|超时/.test(error))
    return '答辩服务暂时繁忙，请稍候点击重试。'
  if (/未配置|未绑定|尚未绑定/.test(error))
    return '答辩服务还没有准备好，请联系管理员后重试。'
  return '答辩暂时中断，请点击重试，或退出后继续这场练习。'
})
const status = computed(() => {
  if (videoFault.value) return '视频预览中断，请重试'
  if (parentGuidance.value) return parentGuidance.value
  return { waiting: '请稍候', speaking: '老师正在提问', listening: '请回答，讲完后停顿三秒', transcribing: '正在识别回答', fault: '语音中断，请重试' }[stage.value]
})
const fault = computed(() => !!props.parentError || videoFault.value || stage.value === 'fault')
const retryIndex = ref(0)
const videoAttempt = ref(0)
let cachedSpeech: Blob | null = null
let preferOfflineAfterFault = false
let retryAudio: Blob | null = null
let endpointedAt = 0
let key = ''
let delivered = false

watch([video, () => props.stream, videoAttempt], async ([el, stream], _, cleanup) => {
  videoReady.value = false
  videoFault.value = false
  if (!el) return
  let current = true
  el.srcObject = stream
  const failed = () => { videoReady.value = false; videoFault.value = true }
  el.addEventListener('error', failed)
  cleanup(() => {
    current = false
    el.removeEventListener('error', failed)
    el.srcObject = null
  })
  try { await el.play(); if (current) videoReady.value = true } catch { if (current) failed() }
}, { flush: 'post' })

async function ask(path: string, question: string, signal: AbortSignal, serverSpeech: boolean) {
  let reported = false
  const started = () => {
    stage.value = 'speaking'
    if (!reported) emit('audioStart')
    reported = true
  }
  // A verified device-local Mandarin voice avoids the round trip for a full WAV.
  // An unverified or cloud-backed system voice never receives student-specific questions.
  const hasLocalMandarin = window.speechSynthesis?.getVoices().some(v => v.localService && /^(zh|cmn)/i.test(v.lang))
  const localFirst = !serverSpeech || hasLocalMandarin || preferOfflineAfterFault
  if (localFirst) {
    try {
      await speakLocally(question, signal, started)
      return
    } catch (error) {
      signal.throwIfAborted()
      if (!serverSpeech || reported) throw error
      preferOfflineAfterFault = false
    }
  }
  try {
    const options = { responseType: 'blob' as const, _silent: true, signal, timeout: 10000 }
    const audio = cachedSpeech || await request.get<Blob, Blob>(path + '/speech', options)
    signal.throwIfAborted()
    await playQuestionAudio(audio, signal, started)
    signal.throwIfAborted()
    cachedSpeech = audio
  } catch (error) {
    signal.throwIfAborted()
    const status = (error as { response?: { status?: number } }).response?.status
    // Ownership/configuration errors stay errors. A synthesis-only rate limit
    // needn't pause playback: this turn already came from the authorized server.
    if (localFirst || reported || (status && status >= 400 && status < 500 && status !== 429)) throw error
    // Only device-local and same-origin offline voices may receive the question.
    await speakLocally(question, signal, started)
    if (!signal.aborted) preferOfflineAfterFault = true
  }
}

async function transcribe(path: string, wav: Blob, signal: AbortSignal) {
  const form = new FormData()
  form.append('file', wav, 'answer.wav')
  const options = { signal, _silent: true, _retryOralRateLimit: true, timeout: 120000 }
  try {
    const { data } = await request.post<unknown, ApiEnvelope<{ text: string }>>(path + '/transcription', form, options)
    return data.text.trim()
  } catch (error) {
    const status = (error as { response?: { status?: number } }).response?.status
    // An unintelligible recording needs a new attempt. Transport failures keep the original WAV.
    if (status === 422) return ''
    throw error
  }
}

async function conduct(sid: number, turn: number, question: string, stream: MediaStream, signal: AbortSignal) {
  const path = `/ai/defense/sessions/${sid}/turns/${turn}`
  const caps = await props.getCapabilities()
  signal.throwIfAborted()
  if (!caps.transcription) throw new Error('请在系统设置绑定语音转写服务')
  const maxSeconds = Math.max(1, Math.min(90, Math.floor(caps.max_recording_seconds || 90)))
  if (!retryAudio) await ask(path, question, signal, caps.speech)
  for (let attempt = 0; attempt < 3; attempt++) {
    signal.throwIfAborted()
    if (!retryAudio) {
      const context = await activeContext(signal)
      const wav = await recordAnswer(stream, context, maxSeconds, signal, () => { stage.value = 'listening' })
      signal.throwIfAborted()
      endpointedAt = performance.now()
      retryAudio = wav
    }
    if (retryAudio) {
      stage.value = 'transcribing'
      const text = await transcribe(path, retryAudio, signal)
      signal.throwIfAborted()
      retryAudio = null
      if (text) {
        delivered = true
        stage.value = 'waiting'
        emit('answer', text, endpointedAt)
        return
      }
    }
    if (attempt < 2) {
      stage.value = 'speaking'
      await speakLocally('刚才没有听清，请再说一遍。', signal)
    }
  }
  throw new Error('没有听清回答')
}

watch([() => props.sessionId, () => props.turnNo, () => props.question, () => props.active,
       () => props.parentError, () => props.stream, videoReady, retryIndex], async (
  [sid, turn, question, active, error, stream, ready], _, cleanup
) => {
  const turnKey = `${sid}-${turn}-${question}`
  if (turnKey !== key) {
    key = turnKey
    cachedSpeech = retryAudio = null
    delivered = false
  }
  stage.value = 'waiting'
  if (!sid || !turn || !question || !active || error || !ready || delivered || document.hidden) return
  const controller = new AbortController()
  cleanup(() => controller.abort())
  try {
    await conduct(sid, turn, question, stream, controller.signal)
  } catch (error) {
    if (!controller.signal.aborted) {
      stage.value = 'fault'
      const spokenError = capsErrorMessage(error)
      void speakLocally(spokenError, controller.signal).catch(() => {})
    }
  }
}, { immediate: true })

function capsErrorMessage(error: unknown) {
  if (error instanceof Error && error.message.startsWith('请在系统设置'))
    return '语音转写还没有设置好，请联系管理员。'
  return '语音暂时中断，请点击重试。'
}

watch(parentGuidance, (message, _, cleanup) => {
  if (!message || document.hidden) return
  const prompt = new AbortController()
  cleanup(() => prompt.abort())
  void speakLocally(message, prompt.signal).catch(() => {})
}, { immediate: true })

function retry() {
  primePracticeVoice()
  if (videoFault.value) videoAttempt.value++
  else if (props.parentError) emit('retry')
  else retryIndex.value++
}

onBeforeUnmount(() => { retryAudio = cachedSpeech = null })
</script>

<template>
  <section class="oral-stage" aria-label="视频模拟答辩" :data-phase="fault ? 'fault' : stage">
    <video ref="video" autoplay playsinline muted aria-label="本人摄像头画面" />
    <div class="vignette" aria-hidden="true"></div>
    <div class="room-controls">
      <div class="room-status" :class="{ listening: stage === 'listening' && !fault }" role="status" :aria-label="status">
        <Warning v-if="fault" />
        <Microphone v-else-if="stage === 'listening'" />
        <Headset v-else-if="stage === 'speaking'" />
        <Loading v-else class="rotating" />
      </div>
      <button v-if="fault" type="button" class="room-action" aria-label="重试" @click="retry"><RefreshRight /></button>
      <button type="button" class="room-action leave" aria-label="离开答辩" @click="emit('leave')"><CloseBold /></button>
    </div>
    <PracticeObservation v-if="observe && sessionId" :key="sessionId" :video="video" :session-id="sessionId"
                         :active="active && videoReady" @signal="emit('signal', $event)" />
  </section>
</template>

<style scoped>
.oral-stage { position: absolute; inset: 0; overflow: hidden; background: #11191b; color: #fff; }
.oral-stage video { display: block; width: 100%; height: 100%; object-fit: contain; transform: scaleX(-1); }
.vignette { position: absolute; inset: 60% 0 0; background: linear-gradient(transparent, rgb(0 0 0 / .36)); pointer-events: none; }
.room-controls { position: absolute; bottom: max(28px, env(safe-area-inset-bottom)); inset-inline: 0; display: flex; align-items: center; justify-content: center; gap: 14px; }
.room-status, .room-action { width: 52px; height: 52px; border-radius: 50%; display: grid; place-items: center; background: #202e31; color: white; }
.room-action { border: 1px solid rgb(255 255 255 / .45); cursor: pointer; }
.room-action:focus-visible { outline: 3px solid white; outline-offset: 3px; }
.room-controls svg { width: 23px; height: 23px; }
.room-status.listening { background: #15614f; box-shadow: 0 0 0 5px rgb(94 222 168 / .24); }
.room-action.leave { background: #a53a3c; }
.rotating { animation: rotate 1.8s linear infinite; }
@keyframes rotate { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .rotating { animation: none; } }
</style>
