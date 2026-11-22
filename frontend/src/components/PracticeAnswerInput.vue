<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import request from '@/api/request'
import { recordAnswer } from '@/utils/practiceRecorder'
import type { ApiEnvelope } from '@/types/api'

const props = defineProps<{ sessionId: number; turnNo: number; canDictate: boolean }>()
const emit = defineEmits<{ recordingStart: []; transcript: [text: string]; submitTranscript: [] }>()

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
type SpeechWindow = Window & { SpeechRecognition?: new () => Dictation; webkitSpeechRecognition?: new () => Dictation }
const speechWindow = window as SpeechWindow
const Recognizer = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
const wavSupported = typeof navigator.mediaDevices?.getUserMedia === 'function'
  && typeof window.AudioContext === 'function' && typeof window.AudioWorkletNode === 'function'
const asr = ref(false)
const maxSeconds = ref(90)
const loading = ref(true)
const direct = ref(false)
const message = ref('')
const state = ref<'idle' | 'permission' | 'recording' | 'transcribing'>('idle')
const elapsed = ref(0)
const retryAudio = shallowRef<Blob | null>(null)
let operation: AbortController | null = null
let native: Dictation | null = null
let finish: (() => void) | null = null
let timer: ReturnType<typeof setInterval> | undefined
let nativeLimit: ReturnType<typeof setTimeout> | undefined
const supported = computed(() => asr.value ? wavSupported : !!Recognizer)
const label = computed(() => ({
  permission: '取消开启麦克风', recording: '结束并转写', transcribing: '取消转写', idle: '语音作答',
}[state.value]))
const capabilities = new AbortController()
onMounted(async () => {
  try {
    const options = { _silent: true, signal: capabilities.signal, timeout: 5000 }
    const { data } = await request.get<unknown, ApiEnvelope<{ transcription: boolean; max_recording_seconds: number }>>(
      '/ai/defense/speech-capabilities', options)
    asr.value = data.transcription
    maxSeconds.value = Math.max(1, Math.min(90, Math.floor(data.max_recording_seconds || 90)))
  } catch { /* Earlier deployments and disconnected services can use browser recognition. */ }
  finally { loading.value = false }
})

function cancel() {
  operation?.abort()
  operation = null
  retryAudio.value = null
  if (native) {
    native.onresult = native.onerror = native.onend = null
    native.abort()
    native = null
  }
  clearInterval(timer)
  clearTimeout(nativeLimit)
  finish = null
  state.value = 'idle'
}

function recording(stop: () => void) {
  state.value = 'recording'
  finish = stop
  elapsed.value = 0
  const start = Date.now()
  timer = setInterval(() => { elapsed.value = Math.min(maxSeconds.value, Math.floor((Date.now() - start) / 1000)) }, 250)
}

function deliver(text: string, submit: boolean) {
  text = text.trim()
  if (!text) { message.value = '没有识别到回答，请重新录音或输入文字。'; return }
  emit('transcript', text)
  if (submit) emit('submitTranscript')
  else message.value = '转写已填入回答，可校对后提交。'
}

function browserDictation(task: AbortController) {
  if (!Recognizer) return
  const session = new Recognizer()
  native = session
  session.lang = 'zh-CN'
  session.continuous = true
  session.interimResults = false
  let text = ''
  let failed = false
  session.onresult = e => {
    if (task.signal.aborted) return
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const result = e.results[i]
      if (result?.isFinal) text += result[0].transcript
    }
  }
  session.onerror = e => {
    failed = true
    message.value = e.error === 'not-allowed' ? '麦克风权限未开启，可使用文字作答。' : '浏览器语音识别中断，可重试或输入文字。'
  }
  session.onend = () => {
    if (task.signal.aborted) return
    native = null
    clearInterval(timer)
    clearTimeout(nativeLimit)
    state.value = 'idle'
    finish = null
    operation = null
    if (text || !failed) deliver(text, direct.value && !failed)
  }
  session.start()
  recording(() => { state.value = 'transcribing'; session.stop() })
  nativeLimit = setTimeout(() => { if (native === session && state.value === 'recording') finish?.() }, maxSeconds.value * 1000)
}

async function toggle(retry = false) {
  if (state.value === 'recording') { finish?.(); return }
  if (state.value !== 'idle') { cancel(); return }
  if (!props.canDictate || !supported.value) return
  const task = new AbortController()
  operation = task
  let wav = retry ? retryAudio.value : null
  retryAudio.value = null
  message.value = ''
  emit('recordingStart')
  state.value = 'permission'
  try {
    if (!asr.value) { browserDictation(task); return }
    wav ??= await recordAnswer(maxSeconds.value, task.signal, recording)
    task.signal.throwIfAborted()
    clearInterval(timer)
    state.value = 'transcribing'
    finish = null
    const form = new FormData()
    form.append('file', wav, 'answer.wav')
    const options = { signal: task.signal, _silent: true, timeout: 120000 }
    const { data } = await request.post<unknown, ApiEnvelope<{ text: string }>>(
      `/ai/defense/sessions/${props.sessionId}/turns/${props.turnNo}/transcription`, form, options)
    task.signal.throwIfAborted()
    operation = null
    state.value = 'idle'
    deliver(data.text, direct.value)
  } catch (err) {
    if (task.signal.aborted) return
    const error = err as { name?: string; message?: string; response?: {status?: number; data?: {message?: string}} }
    cancel()
    const status = error.response?.status
    if (wav && (!status || status === 429 || status >= 500)) retryAudio.value = wav
    message.value = error.name === 'NotAllowedError' ? '麦克风权限未开启，可使用文字作答。'
      : error.response?.data?.message || (error.message?.includes('录音') ? error.message : '语音转写未完成，可重试或输入文字。')
  }
}

watch(() => [props.turnNo, props.sessionId], () => { cancel(); message.value = '' })
watch(() => props.canDictate, allowed => { if (!allowed) cancel() })
onBeforeUnmount(() => { capabilities.abort(); cancel() })
defineExpose({ cancel })
</script>

<template>
  <div v-if="canDictate" class="voice-answer">
    <div class="voice-answer-controls">
      <el-button size="small" :disabled="loading || !supported" :aria-pressed="state !== 'idle'" @click="toggle()">{{ label }}</el-button>
      <el-button v-if="retryAudio" size="small" @click="toggle(true)">重试本段录音</el-button>
      <el-checkbox v-if="supported" v-model="direct" :disabled="state !== 'idle'">转写后直接提交</el-checkbox>
      <span v-if="state === 'recording'" role="status">录音中 {{ elapsed }} / {{ maxSeconds }} 秒</span>
      <span v-if="state === 'transcribing'" role="status">正在转写…</span>
    </div>
    <p v-if="!loading && supported">{{ asr ? '录音发送至系统绑定的语音识别服务' : '语音由浏览器的识别服务处理' }}，最长 {{ maxSeconds }} 秒。默认先校对文字，再提交给 AI 评阅。</p>
    <p v-else-if="!loading">当前浏览器无法启动语音作答，可输入文字继续练习。</p>
    <p v-if="message" role="status">{{ message }}</p>
  </div>
</template>

<style scoped>
.voice-answer { margin: 12px 0; }
.voice-answer-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.voice-answer-controls span, .voice-answer p { font-size: 12px; color: #58687c; }
.voice-answer p { margin: 6px 0; line-height: 1.55; }
</style>
