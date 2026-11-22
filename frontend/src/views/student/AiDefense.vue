<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { Check, Close, Document, Microphone, VideoCamera, VideoPlay } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { aiApi, listKnowledgeMaterials } from '@/api/common'
import { createObservationRecorder, syncObservations, type ObservationSignal } from '@/api/practiceObservations'
import PracticeMedia from '@/components/PracticeMedia.vue'
import { useUserStore } from '@/stores/user'
import type { PracticeSession, PracticeTurn } from '@/types/api'
import { speakLocally, primePracticeVoice, releasePracticeVoice } from '@/utils/practiceVoice'

const router = useRouter()
const route = useRoute()
const user = useUserStore()
const sessions = ref<PracticeSession[]>([])
const materialCount = ref<number | null>(null)
const lobbyError = ref('')
const loading = ref(true)
const observe = ref(false)
const dialog = ref<HTMLDialogElement | null>(null)
const room = ref(false)
const paused = ref(false)
const finished = ref(false)
const stream = shallowRef<MediaStream | null>(null)
const sessionId = ref<number | null>(null)
const turn = shallowRef<PracticeTurn | null>(null)
const busy = ref(false)
const parentError = ref('')
const completionAudio = shallowRef<AbortController | null>(null)
let pendingAnswer = ''
let generation = 0
let deviceRequest = 0
let mounted = true
let observationRecorder: ReturnType<typeof createObservationRecorder> | null = null
const requestedId = computed(() => {
  const id = Number(route.query.session)
  return Number.isSafeInteger(id) && id > 0 ? id : null
})
const requestedSession = computed(() => sessions.value.find(s => s.id === requestedId.value))
const unavailable = computed(() => !loading.value && route.query.session !== undefined && !requestedSession.value)

async function loadLobby() {
  loading.value = true
  try {
    const [materials, history] = await Promise.all([listKnowledgeMaterials(), aiApi.sessions()])
    if (!mounted) return
    materialCount.value = materials.filter(d => d.practice_eligible).length
    sessions.value = history.data
  } catch { if (mounted) lobbyError.value = '未能读取个人练习记录或材料，请刷新重试。' }
  finally { if (mounted) loading.value = false }
}

function recordObservation(signal: ObservationSignal) {
  if (!sessionId.value || !user.user?.id || !observe.value) return
  observationRecorder ??= createObservationRecorder(user.user.id, sessionId.value)
  void observationRecorder(signal)
}

function stopDevices() {
  deviceRequest++
  stream.value?.getTracks().forEach(track => { track.onended = null; track.stop() })
  stream.value = null
}

function closeMedia() {
  stopDevices()
  completionAudio.value?.abort()
  completionAudio.value = null
  releasePracticeVoice()
}

function pauseRoom() {
  if (!room.value || finished.value || paused.value) return
  paused.value = true
  closeMedia()
}

function visibilityChanged() { if (document.hidden) pauseRoom() }

async function leaveRoom() {
  generation++
  closeMedia()
  dialog.value?.close()
  room.value = paused.value = finished.value = busy.value = false
  parentError.value = pendingAnswer = ''
  turn.value = null
  sessionId.value = null
  observationRecorder = null
  if (mounted) {
    if (route.query.session !== undefined) await router.replace({ query: {} })
    await loadLobby()
  }
}

function openReport(id: number) {
  return router.push({ path: '/student/ai-report', query: { session: String(id) } })
}

function complete() {
  finished.value = true
  stopDevices()
  if (sessionId.value && user.user?.id) void syncObservations(user.user.id, sessionId.value)
  if (document.hidden) return
  const sound = new AbortController()
  completionAudio.value = sound
  void speakLocally('本场模拟答辩已结束，谢谢你的回答。', sound.signal).catch(() => {})
}

// Always ask the server for the current unanswered turn. Retrying an acknowledged
// answer or a lost "next" response therefore resumes the same session.
async function advance() {
  if (busy.value || !room.value || paused.value || finished.value || !stream.value) return
  const run = generation
  const currentRun = () => mounted && room.value && run === generation
  busy.value = true
  parentError.value = ''
  try {
    if (sessionId.value === null) {
      const bank = await aiApi.generateQuestions(8)
      if (!currentRun()) return
      const created = await aiApi.createSession(8, bank.data.questions.map((q: { id: number }) => q.id))
      if (!currentRun()) return
      sessionId.value = created.data.session_id
      await router.replace({ query: { session: String(sessionId.value) } })
    }
    const id = sessionId.value
    if (!id || !currentRun()) return
    if (!turn.value) {
      const { data } = await aiApi.sessionDetail(id)
      if (!currentRun()) return
      if (data.status === 'FINISHED') { complete(); return }
      if (data.status === 'CREATED') {
        const started = await aiApi.startSession(id)
        if (currentRun()) turn.value = started.data
        return
      }
    }
    if (pendingAnswer && turn.value) {
      await aiApi.answer(id, { question: turn.value.question, answer: pendingAnswer })
      if (!currentRun()) return
      pendingAnswer = ''
    }
    const { data } = await aiApi.next(id)
    if (!currentRun()) return
    if (data.finished) complete()
    else turn.value = data
  } catch (e) {
    if (currentRun()) {
      const error = e as { response?: { data?: { message?: string } }; message?: string }
      parentError.value = error.response?.data?.message || '答辩服务暂未响应，请重试。'
    }
  } finally { if (currentRun()) busy.value = false }
}

function submitSpeech(text: string) {
  if (!turn.value || busy.value || pendingAnswer || paused.value || finished.value || !text.trim()) return
  pendingAnswer = text.trim().slice(0, 8000)
  void advance()
}

async function acquire() {
  const attempt = ++deviceRequest
  primePracticeVoice()
  paused.value = false
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('请使用支持摄像头和麦克风的浏览器，通过 HTTPS 或本机地址进入。')
    const devices = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    if (!mounted || !room.value || attempt !== deviceRequest || document.hidden) {
      devices.getTracks().forEach(track => track.stop())
      return
    }
    if (!devices.getVideoTracks().some(t => t.readyState === 'live') || !devices.getAudioTracks().some(t => t.readyState === 'live')) {
      devices.getTracks().forEach(track => track.stop())
      throw new Error('摄像头或麦克风不可用。请连接设备后重新进入。')
    }
    stream.value = devices
    devices.getTracks().forEach(track => { track.onended = pauseRoom })
    if (!turn.value || pendingAnswer || parentError.value) await advance()
  } catch (e) {
    if (attempt !== deviceRequest || !mounted) return
    const error = e as Error
    lobbyError.value = error.name === 'NotAllowedError' ? '进入视频答辩需要摄像头和麦克风权限。请允许两项权限后重试。'
      : error.name === 'NotFoundError' ? '未找到摄像头或麦克风。请连接两种设备后重试。'
      : error.name === 'NotReadableError' ? '设备被其他应用占用或暂不可用，请检查后重试。'
      : error.message || '音视频设备未能启动，请重试。'
    await leaveRoom()
  }
}

function enter(id: number | null = null) {
  if (room.value || loading.value) return
  sessionId.value = id
  turn.value = null
  pendingAnswer = parentError.value = lobbyError.value = ''
  room.value = true
  generation++
  void nextTick(() => { if (room.value && !dialog.value?.open) dialog.value?.showModal() })
  void acquire()
}

onMounted(() => {
  void loadLobby()
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('pagehide', pauseRoom)
})
onBeforeUnmount(() => {
  mounted = false
  generation++
  closeMedia()
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('pagehide', pauseRoom)
})
</script>

<template>
  <header class="student-heading"><h1>AI 模拟答辩</h1></header>
  <section class="page-card lobby">
    <div class="lobby-icon" aria-hidden="true"><el-icon><VideoCamera /></el-icon></div>
    <h2>视频答辩</h2>
    <p>准备好摄像头和麦克风。听完提问后直接回答，讲完停顿约三秒，系统会自动转写并继续。</p>
    <p v-if="materialCount">已就绪 {{ materialCount }} 份本课题材料。题目和评分依据取自本人的报告与源码。</p>
    <p v-else-if="materialCount === 0">请先在知识准备中上传报告或源码，等待索引完成。</p>
    <details class="environment">
      <summary>设备与数据使用</summary>
      <p>镜头画面在本机预览。回答录音发送给系统绑定的转写服务，相关材料与转写文字用于模型评阅。</p>
      <el-checkbox v-model="observe">在练习报告中记录镜头人数和切页提醒</el-checkbox>
    </details>
    <p v-if="lobbyError || unavailable" role="alert" class="lobby-error">{{ lobbyError || '这场练习不可用，请从本人的练习记录进入。' }}</p>
    <div class="lobby-actions">
      <el-button v-if="requestedSession?.status === 'FINISHED'" type="primary" @click="openReport(requestedSession.id)">查看练习报告</el-button>
      <el-button v-else-if="requestedSession" type="primary" :loading="loading" @click="enter(requestedSession.id)">继续视频答辩</el-button>
      <el-button v-else type="primary" :loading="loading" :disabled="!materialCount" @click="enter()">开始视频答辩</el-button>
      <el-button @click="router.push('/student/ai-knowledge')">管理练习材料</el-button>
      <el-button v-if="!materialCount || lobbyError" link @click="lobbyError = ''; loadLobby()">刷新</el-button>
    </div>
  </section>
  <section v-if="sessions.length" class="page-card history">
    <h2>练习记录</h2>
    <div v-for="s in sessions" :key="s.id" class="session-row">
      <span>答辩 #{{ s.id }}</span><span>已回答 {{ s.answered_count }} / {{ s.question_count }} 题</span>
      <el-button v-if="s.status === 'FINISHED'" link type="primary" @click="openReport(s.id)">查看报告</el-button>
      <el-button v-else link type="primary" @click="enter(s.id)">继续视频答辩</el-button>
    </div>
  </section>
  <Teleport to="body">
    <dialog v-if="room" ref="dialog" class="oral-room" aria-label="视频模拟答辩" @cancel.prevent="leaveRoom">
      <section v-if="finished" class="room-overlay" aria-label="答辩已结束">
        <el-icon class="complete-icon"><Check /></el-icon>
        <div class="room-buttons">
          <button class="room-button" aria-label="查看练习报告" @click="sessionId && openReport(sessionId)"><el-icon><Document /></el-icon></button>
          <button class="room-button leave" aria-label="退出视频答辩" @click="leaveRoom"><el-icon><Close /></el-icon></button>
        </div>
      </section>
      <PracticeMedia v-else-if="stream && !paused" :stream="stream" :session-id="sessionId" :turn-no="turn?.turn_no ?? null"
                     :question="turn?.question || ''" :active="!!turn && !busy && !parentError" :parent-error="parentError"
                     :observe="observe" @answer="submitSpeech" @retry="advance" @leave="leaveRoom" @signal="recordObservation" />
      <section v-else class="room-overlay" :aria-label="paused ? '已暂停答辩' : '等待摄像头和麦克风授权'">
        <el-icon class="device-icon"><Microphone /></el-icon>
        <div class="room-buttons">
          <button v-if="paused" class="room-button" aria-label="恢复视频答辩" @click="acquire"><el-icon><VideoPlay /></el-icon></button>
          <button class="room-button leave" aria-label="退出视频答辩" @click="leaveRoom"><el-icon><Close /></el-icon></button>
        </div>
      </section>
    </dialog>
  </Teleport>
</template>

<style scoped>
:global(body:has(dialog.oral-room[open]) #app) { visibility: hidden; }
.lobby, .history { padding: clamp(18px, 3vw, 32px); }
.lobby-icon { color: #146cb3; font-size: 30px; }
.lobby h2, .history h2 { margin: 10px 0; font-size: 20px; }
.lobby p, .session-row { color: #455a70; line-height: 1.6; }
.lobby .lobby-error { color: #a93c23; }
.environment { margin: 12px 0; color: #58687c; font-size: 14px; }
.environment summary { cursor: pointer; width: fit-content; }
.lobby-actions, .session-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.lobby-actions .el-button, .session-row .el-button { margin: 0; }
.history { margin-top: 16px; }
.session-row { padding: 9px 0; border-bottom: 1px solid #e6ebee; }
.oral-room { position: fixed; inset: 0; width: 100%; max-width: none; height: 100dvh; max-height: none; margin: 0; padding: 0; border: 0; background: #10191c; color: white; overflow: hidden; }
.oral-room::backdrop { background: #10191c; }
.room-overlay { height: 100%; display: grid; align-content: center; justify-items: center; gap: 32px; }
.room-buttons { display: flex; gap: 16px; }
.room-button { cursor: pointer; background: #264b55; color: white; border: 0; border-radius: 50%; width: 58px; height: 58px; font-size: 24px; }
.room-button.leave { background: #b53b3c; }
.room-button:focus-visible { outline: 3px solid white; outline-offset: 4px; }
.complete-icon, .device-icon { color: #9dd6cf; font-size: 44px; }
</style>
