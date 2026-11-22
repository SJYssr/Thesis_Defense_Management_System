<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { aiApi, listKnowledgeMaterials, uploadApi } from '@/api/common'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
import PracticeMedia from '@/components/PracticeMedia.vue'
import PracticeAssessment from '@/components/PracticeAssessment.vue'
import PracticeSources from '@/components/PracticeSources.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { AiDocument, PracticeMaterialKind, UploadPolicy, PracticeQuestion, PracticeSession, PracticeTurn } from '@/types/api'

const router = useRouter()
const route = useRoute()
const docs = ref<AiDocument[]>([])
const maxMaterials = 30 // POST /ai/defense/questions/generate 的 document_ids 上限
const selected = ref<number[]>([])
const policy = ref<UploadPolicy | null>(null)
const uploading = ref<PracticeMaterialKind | null>(null)
const retrying = ref<number | null>(null)
const uploadKinds: { kind: PracticeMaterialKind; label: string }[] = [
  { kind: 'REPORT', label: '上传报告草稿' }, { kind: 'SOURCE_CODE', label: '上传项目源码' },
]
let poll: number | undefined
const documentsLoaded = ref(false)
const documentsBusy = ref(false)
const sessions = ref<PracticeSession[]>([])
const questions = ref<(PracticeQuestion & { id: number })[]>([])
const questionCount = ref(8)
const sessionId = ref<number | null>(null)
const sessionCount = ref(0)
const turns = ref<PracticeTurn[]>([])
const answer = ref('')
const busy = ref(false)
const phase = ref<'idle' | 'answering' | 'feedback'>('idle')
let onPage = true
onBeforeUnmount(() => { onPage = false; window.clearTimeout(poll) })

const TYPE_CN: Record<string, string> = {
  BASIC: '基础理解', TECHNOLOGY: '技术选型', DESIGN: '系统设计', IMPLEMENTATION: '实现细节',
  DATABASE: '数据库', TEST: '测试', INNOVATION: '创新', LIMITATION: '不足', EXTENSION: '扩展',
}
const current = computed(() => turns.value.at(-1))
const mainCount = computed(() => turns.value.filter(t => !t.is_follow_up).length)
const completed = computed(() => turns.value.filter(t => t.student_answer != null))
const eligibleCount = computed(() => docs.value.filter(d => d.practice_eligible).length)
const scoreDimensions = computed(() => {
  const s = current.value?.scores
  return s ? [
    { name: '准确性', value: s.accuracy, max: 35 }, { name: '逻辑性', value: s.logic, max: 25 },
    { name: '表达清晰', value: s.clarity, max: 20 }, { name: '技术深度', value: s.depth, max: 20 },
  ] : []
})

async function loadPolicy() {
  try { policy.value = (await uploadApi.policy()).data } catch { policy.value = null }
}

function uploadLimit(kind: PracticeMaterialKind) {
  return policy.value?.practice_max_upload_size_mb?.[kind] ?? policy.value?.max_upload_size_mb ?? 0
}

async function uploadPractice(kind: PracticeMaterialKind, file: File) {
  if (!policy.value || uploading.value) return false
  const allowed = policy.value.practice?.[kind] || []
  const name = file.name.toLowerCase()
  if (!allowed.some(ext => name.endsWith(ext.toLowerCase()))) {
    ElMessage.warning('请选择支持的文件格式：' + allowed.join('、'))
    return false
  }
  const limit = uploadLimit(kind)
  if (limit > 0 && file.size > limit * 1024 * 1024) {
    ElMessage.warning('单文件大小上限为 ' + limit + ' MB')
    return false
  }
  uploading.value = kind
  try {
    await aiApi.uploadPracticeMaterial(kind, file)
    ElMessage.success('已添加到练习材料库')
  } catch { /* 索引失败时，文档可能已保存，可从列表重试。 */ }
  finally {
    uploading.value = null
    if (onPage) await loadDocuments()
  }
  return false
}

async function retryIndex(id: number) {
  if (retrying.value != null) return
  retrying.value = id
  try { await aiApi.indexDocument(id) } catch { /* 请求层已显示原因。 */ }
  finally {
    retrying.value = null
    if (onPage) await loadDocuments()
  }
}

async function loadDocuments() {
  window.clearTimeout(poll)
  if (documentsBusy.value) return
  documentsBusy.value = true
  try {
    const all = await listKnowledgeMaterials()
    if (!onPage) return
    const previous = new Set(docs.value.filter(d => d.practice_eligible).map(d => d.id))
    const eligible = new Set(all.filter(d => d.practice_eligible).map(d => d.id))
    const retained = selected.value.filter(id => eligible.has(id))
    const added = [...eligible].filter(id => !previous.has(id))
    selected.value = [...retained, ...added].slice(0, maxMaterials)
    docs.value = all
    documentsLoaded.value = true
    questions.value = []
    if (phase.value === 'idle' && all.some(d => ['WAITING', 'PROCESSING'].includes(d.index_status))) {
      poll = window.setTimeout(loadDocuments, 4000)
    }
  } catch { /* 请求层已显示失败原因，保留上次列表以便重试。 */ }
  finally { documentsBusy.value = false }
}

async function loadSessions() {
  try { sessions.value = (await aiApi.sessions()).data } catch { /* 保留练习记录并由请求层提示。 */ }
}

async function generate() {
  const { data } = await aiApi.generateQuestions([...selected.value], questionCount.value)
  questions.value = data.questions
}

async function previewQuestions() {
  if (busy.value || !selected.value.length) return
  busy.value = true
  try { await generate() } catch { questions.value = [] } finally { busy.value = false }
}

function showTurns(sid: number, count: number, values: PracticeTurn[]) {
  sessionId.value = sid
  sessionCount.value = count
  turns.value = values
  answer.value = ''
  phase.value = current.value?.student_answer != null ? 'feedback' : 'answering'
}

function viewReport(id: number) {
  if (!onPage) return
  return router.push({ path: '/student/ai-report', query: { session: String(id) } })
}

async function openSession(id: number) {
  const { data } = await aiApi.sessionDetail(id)
  if (!onPage) return
  if (data.status === 'FINISHED') { await viewReport(id); return }
  const values = data.status === 'CREATED' ? [(await aiApi.startSession(id)).data] : data.turns
  if (!onPage) return
  if (!values?.length) {
    ElMessage.warning('当前会话没有可恢复的题目，请重新生成练习。')
    return
  }
  showTurns(id, data.question_count, values)
  await router.replace({ query: { session: String(id) } })
}

async function resume(id: number) {
  if (busy.value) return
  busy.value = true
  try { await openSession(id) } catch { /* 可在练习记录重试。 */ } finally { busy.value = false }
}

async function start() {
  if (busy.value || !selected.value.length || !questionCount.value) return
  busy.value = true
  try {
    if (!questions.value.length) await generate()
    if (!onPage) return
    const { data } = await aiApi.createSession(questionCount.value, questions.value.map(q => q.id))
    questions.value = []
    if (onPage) await openSession(data.session_id)
  } catch {
    questions.value = []
    await loadSessions()
  } finally { busy.value = false }
}

function appendTranscript(text: string) {
  if (phase.value === 'answering' && !busy.value) answer.value = (answer.value + text).slice(0, 8000)
}

async function submitAnswer() {
  const turn = current.value
  const sid = sessionId.value
  const submitted = answer.value.trim()
  if (!turn || !sid || busy.value || submitted.length < 5) return
  busy.value = true
  try {
    const { data } = await aiApi.answer(sid, { question: turn.question, answer: submitted })
    turn.student_answer = submitted
    turn.ai_feedback = data.feedback
    turn.scores = data.scores
    turn.total_score = data.scores.total
    turn.sources = data.sources
    turn.assessment = data.assessment
    phase.value = 'feedback'
  } catch { /* 评价失败时保留回答，允许重试。 */ } finally { busy.value = false }
}

async function next() {
  const sid = sessionId.value
  if (!sid || busy.value) return
  busy.value = true
  try {
    const { data } = await aiApi.next(sid)
    if (data.finished) { await viewReport(sid); return }
    answer.value = ''
    turns.value.push(data)
    phase.value = 'answering'
  } catch { /* 保留反馈和继续按钮。 */ } finally { busy.value = false }
}

async function backToList() {
  if (phase.value === 'answering' && answer.value.trim()) {
    try {
      await ElMessageBox.confirm('本轮文字尚未提交。离开后可从练习记录继续回答这道题。', '离开当前练习？', {
        confirmButtonText: '放弃草稿并离开', cancelButtonText: '继续作答',
      })
    } catch { return }
  }
  sessionId.value = null
  turns.value = []
  answer.value = ''
  phase.value = 'idle'
  await router.replace({ query: {} })
  await loadSessions()
}

onMounted(() => {
  void loadPolicy()
  void loadDocuments()
  void loadSessions()
  const id = Number(route.query.session)
  if (Number.isSafeInteger(id) && id > 0) void resume(id)
})
</script>
<template>
  <div class="ai-defense">
    <header class="page-heading">
      <h1>AI 模拟答辩</h1>
      <p>选用自己的报告和源码练习答辩，练习分数仅用于复盘。</p>
    </header>

    <template v-if="phase === 'idle'">
      <section class="page-card setup">
        <div class="setup-head">
          <h2>本次练习材料</h2>
          <el-button link type="primary" :loading="documentsBusy" :disabled="busy" @click="loadDocuments">刷新材料</el-button>
        </div>
        <p>这里上传的报告草稿和源码存入个人练习库，正式材料请在「毕业材料」中提交。
          系统会把材料文本发送给已配置的向量服务进行索引，所选材料中的相关片段和你的回答会发送给对话模型。</p>
        <div class="practice-uploads">
          <div v-for="item in uploadKinds" :key="item.kind">
            <el-upload :show-file-list="false" :accept="policy?.practice?.[item.kind]?.join(',')"
                       :disabled="!policy?.practice?.[item.kind]?.length || !!uploading || busy || documentsBusy"
                       :before-upload="(file: File) => uploadPractice(item.kind, file)">
              <el-button :loading="uploading === item.kind" :disabled="!policy?.practice?.[item.kind]?.length || !!uploading || busy || documentsBusy">{{ item.label }}</el-button>
            </el-upload>
            <small v-if="policy?.practice?.[item.kind]?.length">
              {{ policy.practice[item.kind].join('、') }} · {{ uploadLimit(item.kind) > 0 ? '单文件 ≤ ' + uploadLimit(item.kind) + ' MB' : '单文件不限大小' }}
            </small>
          </div>
          <el-button v-if="!policy" link type="primary" @click="loadPolicy">重新读取上传设置</el-button>
        </div>
        <p v-if="documentsLoaded && !eligibleCount">索引完成后即可选择材料开始练习。审核已通过的阶段报告可在知识准备中同步。</p>
        <p v-else-if="eligibleCount">已选 {{ selected.length }} 份材料。题目和评价会附上可核对的原文片段。<span v-if="eligibleCount > maxMaterials">每次最多选 {{ maxMaterials }} 份。</span></p>
        <el-checkbox-group v-if="docs.length" v-model="selected" :max="maxMaterials" class="document-list" @change="questions = []">
          <el-checkbox v-for="d in docs" :key="d.id" :value="d.id" :disabled="!d.practice_eligible || busy || documentsBusy">
            <span>{{ d.document_name }}</span>
            <small>{{ d.practice_upload ? '练习上传 · ' : '' }}{{ d.practice_eligible ? (d.chunk_count || 0) + ' 个片段' : (d.index_status === 'SUCCESS' ? '不属于本次练习范围' : (d.status_cn || '尚未就绪')) }}</small>
          </el-checkbox>
        </el-checkbox-group>
        <div v-for="d in docs.filter(d => d.index_status === 'FAILED')" :key="d.id" class="index-error" role="status">
          <span>{{ d.document_name }} · {{ d.error_message || '索引未完成' }}</span>
          <el-button link type="primary" :loading="retrying === d.id" :disabled="busy || retrying != null" @click="retryIndex(d.id)">重试索引</el-button>
        </div>
        <div class="material-links">
          <el-button link type="primary" @click="router.push('/student/ai-knowledge')">知识准备与索引</el-button>
        </div>
        <div class="start-controls">
          <label for="practice-count">正题数量</label>
          <el-input-number id="practice-count" v-model="questionCount" :min="3" :max="22" :disabled="busy" @change="questions = []" />
          <el-button :disabled="busy || documentsBusy || !!uploading || !selected.length || !questionCount" @click="previewQuestions">预览题库</el-button>
          <el-button type="primary" :loading="busy" :disabled="documentsBusy || !!uploading || !selected.length || !questionCount" @click="start">开始模拟答辩</el-button>
        </div>
        <details v-if="questions.length" class="plan" open>
          <summary>练习题库（{{ questions.length }} 题）</summary>
          <ol>
            <li v-for="q in questions" :key="q.id">{{ q.question }}<PracticeSources :sources="q.sources" /></li>
          </ol>
        </details>
      </section>

      <section v-if="sessions.length" class="page-card history">
        <h2>练习记录</h2>
        <div v-for="s in sessions" :key="s.id" class="session-row">
          <b>答辩 #{{ s.id }}</b><StatusTag :status="s.status" />
          <span>正题 {{ s.answered_count }} / {{ s.question_count }}</span>
          <span v-if="s.overall_score != null">{{ s.overall_score }} 分</span>
          <el-button v-if="s.status === 'FINISHED'" link type="primary" @click="viewReport(s.id)">查看报告</el-button>
          <el-button v-else link type="primary" :disabled="busy" @click="resume(s.id)">继续练习</el-button>
        </div>
      </section>
    </template>

    <div v-else-if="current" class="defense-grid">
      <section class="page-card main-card">
        <div class="turn-heading">
          <span>{{ current.is_follow_up ? '追问' : '正题' }} · {{ TYPE_CN[current.question_type || ''] || '综合答辩' }}</span>
          <span>第 {{ mainCount }} / {{ sessionCount }} 题</span>
        </div>
        <h2 class="question">{{ current.question }}</h2>
        <PracticeSources :sources="current.sources" />
        <PracticeMedia :question="current.question" :can-dictate="phase === 'answering' && !busy" @transcript="appendTranscript" />
        <template v-if="phase === 'answering'">
          <label for="practice-answer" class="answer-label">你的回答</label>
          <el-input id="practice-answer" v-model="answer" type="textarea" :rows="7" maxlength="8000" show-word-limit
                    :disabled="busy" placeholder="结合所选材料说明你的判断和依据，至少输入 5 个字。" />
          <div class="actions">
            <el-button type="primary" :loading="busy" :disabled="answer.trim().length < 5" @click="submitAnswer">提交回答</el-button>
          </div>
        </template>
        <template v-else>
          <div class="feedback">
            <h3>你的回答</h3><p class="answer">{{ current.student_answer }}</p>
            <h3>本轮反馈</h3>
            <PracticeAssessment :assessment="current.assessment" :sources="current.sources" />
            <MarkdownViewer :content="current.ai_feedback || ''" />
          </div>
          <div class="actions"><el-button type="primary" :loading="busy" @click="next">继续答辩</el-button></div>
        </template>
      </section>

      <aside class="page-card practice-progress">
        <h2>答辩进度</h2>
        <ol v-if="completed.length">
          <li v-for="t in completed" :key="t.turn_no">
            <span>第 {{ t.turn_no }} 轮 · {{ t.is_follow_up ? '追问' : (TYPE_CN[t.question_type || ''] || '综合答辩') }}</span>
            <b v-if="t.total_score != null">{{ t.total_score }} 分</b>
          </li>
        </ol>
        <p v-else>正在回答第一题</p>
        <template v-if="phase === 'feedback' && current.scores">
          <h3>本轮得分</h3>
          <div v-for="d in scoreDimensions" :key="d.name" class="dimension">
            <span>{{ d.name }}</span><b>{{ d.value }} / {{ d.max }}</b>
          </div>
        </template>
        <el-button :disabled="busy" @click="backToList">返回练习记录</el-button>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.ai-defense { color: #243449; }
.page-heading { margin-bottom: 16px; }
.page-heading h1 { font-size: 26px; margin: 0 0 6px; }
.ai-defense p { color: #5d6d81; font-size: 13px; line-height: 1.7; }
.page-heading p { margin: 0; }
.ai-defense h2 { margin: 0; font-size: 18px; }
.ai-defense h3 { font-size: 15px; margin: 14px 0 6px; }
.page-card { min-width: 0; }
.setup-head, .session-row, .turn-heading, .dimension, .practice-progress li { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.document-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(270px, 100%), 1fr)); gap: 8px; margin: 16px 0 8px; max-height: 360px; overflow: auto; }
.document-list :deep(.el-checkbox) { height: auto; min-width: 0; padding: 10px 12px; border: 1px solid #dfe6ee; border-radius: 6px; margin: 0; }
.document-list :deep(.el-checkbox.is-checked) { background-color: #f2f7ff; border-color: #a9c7ee; }
.document-list :deep(.el-checkbox__label) { min-width: 0; white-space: normal; overflow-wrap: anywhere; }
.document-list small { display: block; font-size: 12px; padding-top: 4px; color: #63748c; }
.practice-uploads { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.practice-uploads small { display: block; font-size: 12px; margin-top: 3px; color: #63748c; }
.index-error { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; font-size: 13px; color: #88550e; padding: 6px 0; overflow-wrap: anywhere; }
.material-links, .start-controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.start-controls { padding-top: 18px; border-top: 1px solid #e5ebf1; margin-top: 16px; }
.start-controls label { font-size: 14px; }
.start-controls .el-button, .material-links .el-button { margin: 0; }
.plan { margin-top: 18px; font-size: 14px; }
.plan summary { cursor: pointer; }
.plan li { margin: 10px 0; }
.history { margin-top: 18px; }
.session-row { justify-content: flex-start; flex-wrap: wrap; border-bottom: 1px solid #e5ebf1; padding: 10px 0; font-size: 13px; }
.session-row:last-child { border: 0; }
.session-row .el-button { margin-left: auto; }
.defense-grid { display: grid; grid-template-columns: minmax(0, 2.6fr) minmax(220px, 1fr); gap: 16px; align-items: start; }
.turn-heading { color: #356593; font-size: 13px; }
.ai-defense .question { line-height: 1.65; margin: 10px 0 15px; font-size: 21px; overflow-wrap: anywhere; }
.answer-label { display: block; margin-bottom: 8px; font-size: 14px; font-weight: 600; }
.feedback, .answer { white-space: normal; overflow-wrap: anywhere; }
.feedback .answer { white-space: pre-wrap; }
.actions { display: flex; justify-content: flex-end; margin-top: 16px; }
.practice-progress ol { padding: 0; margin: 15px 0; max-height: 320px; overflow: auto; }
.practice-progress li, .dimension { padding: 6px 0; font-size: 13px; }
.practice-progress .el-button { margin-top: 14px; }
@media (max-width: 850px) { .defense-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
