<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { aiApi } from '@/api/common'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
import StatusTag from '@/components/StatusTag.vue'

const router = useRouter()

// ---------- 知识库检查 ----------
const docReady = ref(false)
const docs = ref<any[]>([])

async function checkDocs() {
  const page = await aiApi.listDocuments(1, 100)
  docs.value = page.data.items
  docReady.value = page.data.items.some((d) => d.index_status === 'SUCCESS')
}

// ---------- 问题生成 ----------
const generating = ref(false)
const questions = ref<any[]>([])

async function generateQuestions() {
  generating.value = true
  try {
    const res = await aiApi.generateQuestions()
    questions.value = res.data.questions
    ElMessage.success(`已生成 ${res.data.count} 个维度化答辩问题`)
  } catch {
    /* 已提示 */
  } finally {
    generating.value = false
  }
}

// ---------- 答辩会话 ----------
type Phase = 'idle' | 'answering' | 'feedback' | 'finished'
const phase = ref<Phase>('idle')
const sessionId = ref<number | null>(null)
const questionCount = ref(8)
const currentQuestion = ref<any>(null)
const answer = ref('')
const evaluating = ref(false)
const evaluation = ref<any>(null)
const turns = reactive<any[]>([])
const report = ref<any>(null)
const sessions = ref<any[]>([])

const TYPE_CN: Record<string, string> = {
  BASIC: '基础理解', TECHNOLOGY: '技术选型', DESIGN: '系统设计', IMPLEMENTATION: '实现细节',
  DATABASE: '数据库', TEST: '测试', INNOVATION: '创新', LIMITATION: '不足', EXTENSION: '扩展',
}

async function loadSessions() {
  const res = await aiApi.sessions()
  sessions.value = res.data
}

async function startDefense() {
  if (!docReady.value) {
    ElMessage.warning('请先在「毕业材料」页上传论文并完成索引')
    return
  }
  if (!questions.value.length) {
    await generateQuestions()
  }
  const created = await aiApi.createSession(questionCount.value)
  sessionId.value = created.data.session_id
  const started = await aiApi.startSession(created.data.session_id)
  currentQuestion.value = started.data
  phase.value = 'answering'
  turns.length = 0
  report.value = null
  ElMessage.success('模拟答辩已开始')
}

async function submitAnswer() {
  if (answer.value.trim().length < 5) {
    ElMessage.warning('请输入至少 5 个字的回答')
    return
  }
  evaluating.value = true
  try {
    const res = await aiApi.answer(sessionId.value as number, { question: currentQuestion.value.question, answer: answer.value })
    evaluation.value = res.data
    phase.value = 'feedback'
    turns.push({
      question: currentQuestion.value.question,
      question_type: currentQuestion.value.question_type,
      answer: answer.value,
      scores: res.data.scores,
      feedback: res.data.feedback,
      sources: res.data.sources,
    })
  } catch {
    /* 已提示 */
  } finally {
    evaluating.value = false
  }
}

async function nextQuestion() {
  const res = await aiApi.next(sessionId.value as number)
  const data = res.data
  answer.value = ''
  evaluation.value = null
  if (data.finished) {
    report.value = data.report
    phase.value = 'finished'
    router.push({ path: '/student/ai-report', query: { session: String(sessionId.value) } })
    await loadSessions()
    ElMessage.success('答辩结束, 报告已生成')
  } else {
    currentQuestion.value = data
    phase.value = 'answering'
  }
}

function viewReport(sid: number) { router.push({ path: '/student/ai-report', query: { session: String(sid) } }) }

const currentNo = computed(() => turns.length + (phase.value === 'answering' ? 1 : 0))

const scoreDimensions = computed(() => {
  if (!evaluation.value) return []
  const s = evaluation.value.scores
  return [
    { name: '准确性', value: s.accuracy, max: 35 },
    { name: '逻辑性', value: s.logic, max: 25 },
    { name: '表达清晰', value: s.clarity, max: 20 },
    { name: '技术深度', value: s.depth, max: 20 },
  ]
})

onMounted(async () => {
  await checkDocs()
  await loadSessions()
})
</script>

<template>
  <div class="ai-defense">
    <header class="page-heading"><h1>AI 模拟答辩</h1><p>基于当前论文与阶段材料进行个性化答辩训练</p></header>

    <!-- 未开始: 引导 -->
    <div v-if="phase === 'idle'" class="page-card idle-card">
      <div class="idle-ready" :class="{ ok: docReady }">
        <span class="ir-dot" />{{ docReady ? '知识库就绪：将基于您的论文与材料生成针对性答辩问题' : '知识库为空：请先在「毕业材料」页上传毕业论文，等待索引完成后开始' }}
      </div>

      <div class="idle-grid">
        <div class="idle-main">
          <h3>开始一次模拟答辩</h3>
          <p class="idle-desc">AI 将按「基础理解 / 技术选型 / 系统设计 / 实现细节 / 创新 / 不足」等维度提问，并结合论文原文评价你的回答，支持动态追问。</p>
          <div class="idle-controls">
            <div class="count-field">
              <span>题目数量</span>
              <el-input-number v-model="questionCount" :min="3" :max="22" />
            </div>
            <el-button :loading="generating" :disabled="!docReady" @click="generateQuestions">生成问题库</el-button>
            <el-button type="primary" size="large" :disabled="!docReady" @click="startDefense">开始模拟答辩</el-button>
            <el-button link type="primary" @click="router.push('/student/ai-knowledge')">查看知识准备</el-button>
          </div>

          <template v-if="questions.length">
            <el-divider>已生成 {{ questions.length }} 个问题（按维度）</el-divider>
            <div class="q-chips">
              <span v-for="(q, i) in questions" :key="i" class="q-chip">{{ TYPE_CN[q.question_type] || q.question_type }}</span>
            </div>
          </template>
        </div>

        <div class="idle-side">
          <h4>知识文档（{{ docs.length }}）</h4>
          <div v-for="d in docs" :key="d.id" class="doc-row">
            <span class="doc-name" :title="d.document_name">{{ d.document_name }}</span>
            <StatusTag :status="d.index_status" />
          </div>
          <el-empty v-if="!docs.length" description="暂无知识文档" :image-size="55" />
        </div>
      </div>

      <template v-if="sessions.length">
        <el-divider>历史会话</el-divider>
        <div class="session-list">
          <div v-for="s in sessions" :key="s.id" class="session-row">
            <span class="se-id">答辩 #{{ s.id }}</span>
            <StatusTag :status="s.status" />
            <span class="se-meta">已答 {{ s.answered_count }} 题</span>
            <span v-if="s.overall_score != null" class="se-score">{{ s.overall_score }} 分</span>
            <span class="se-time">{{ (s.finished_at || '-').replace('T', ' ').slice(0, 16) }}</span>
            <el-button v-if="s.status === 'FINISHED'" link type="primary" @click="viewReport(s.id)">查看报告</el-button>
          </div>
        </div>
      </template>
    </div>

    <!-- 答辩进行中 -->
    <div v-else-if="phase === 'answering' || phase === 'feedback'" class="defense-grid">
      <!-- 左: 答辩进度 -->
      <aside class="page-card side-card">
        <h3 class="side-title">答辩进度</h3>
        <div class="big-counter">第 <b>{{ currentNo }}</b> / {{ questionCount }} 题</div>
        <div class="turn-list">
          <div v-for="(t, i) in turns" :key="i" class="turn-item" :class="t.scores.total >= 60 ? 'good' : 'bad'">
            <i class="ti-icon">{{ t.scores.total >= 60 ? '✓' : '✕' }}</i>
            <div class="ti-body">
              <span class="ti-name">{{ TYPE_CN[t.question_type] || `第 ${i + 1} 题` }}</span>
              <small>{{ t.scores.total >= 60 ? '通过' : '待加强' }} · {{ t.scores.total }} 分</small>
            </div>
          </div>
          <div v-if="phase === 'answering'" class="turn-item current">
            <i class="ti-icon num">{{ currentNo }}</i>
            <div class="ti-body">
              <span class="ti-name">{{ TYPE_CN[currentQuestion?.question_type] || '当前题目' }}</span>
              <small>回答中</small>
            </div>
          </div>
        </div>
      </aside>

      <!-- 中: AI 答辩老师 -->
      <section class="page-card main-card">
        <h3 class="side-title">AI 答辩老师</h3>
        <div class="question-card">
          <span class="q-label">问题 {{ currentNo }} · {{ TYPE_CN[currentQuestion?.question_type] || '综合' }}{{ currentQuestion?.is_follow_up ? ' · 追问' : '' }}</span>
          <h4 class="q-text">{{ currentQuestion?.question }}</h4>
          <p class="q-hint">请结合具体业务场景说明，而不是只回答结论。</p>
        </div>

        <template v-if="phase === 'answering'">
          <el-input v-model="answer" type="textarea" :rows="7" class="answer-input" placeholder="输入你的回答……" />
          <div class="main-actions">
            <el-button type="primary" :loading="evaluating" @click="submitAnswer">
              {{ evaluating ? 'AI 评价中…' : '提交回答' }}
            </el-button>
          </div>
        </template>

        <template v-else>
          <h4 class="fb-heading">上一轮反馈</h4>
          <div class="fb-card">
            <div class="fb-label">你的回答</div>
            <p class="fb-answer">{{ turns[turns.length - 1]?.answer }}</p>
            <div class="fb-label">AI 评价</div>
            <div class="fb-feedback markdown-body"><MarkdownViewer :content="evaluation?.feedback || ''" /></div>
          </div>
          <div class="main-actions">
            <el-button type="primary" @click="nextQuestion">下一题</el-button>
          </div>
        </template>
      </section>

      <!-- 右: 实时评价 -->
      <aside class="page-card side-card">
        <h3 class="side-title">实时评价</h3>
        <template v-if="evaluation">
          <div v-for="d in scoreDimensions" :key="d.name" class="dim-row">
            <div class="dim-head"><span>{{ d.name }}</span><b>{{ Math.round((d.value / d.max) * 100) }}%</b></div>
            <el-progress :percentage="Math.round((d.value / d.max) * 100)" :stroke-width="8" :show-text="false" color="#1878fd" />
          </div>
          <div class="total-row">
            <span>本轮总分</span>
            <b :style="{ color: evaluation.scores.total >= 60 ? '#16a34a' : '#e74c3c' }">{{ evaluation.scores.total }}</b>
          </div>
        </template>
        <el-empty v-else description="提交回答后显示评分" :image-size="60" />

        <template v-if="evaluation?.sources?.length">
          <h4 class="src-heading">论文依据</h4>
          <div v-for="s in evaluation.sources" :key="s.chunk_id" class="src-row">
            · {{ s.section || s.document_type }} / chunk#{{ s.chunk_id }}
          </div>
        </template>
      </aside>
    </div>

    <div v-else class="page-card"><el-result icon="success" title="答辩已完成"><template #extra><el-button type="primary" @click="viewReport(sessionId!)">查看答辩报告</el-button></template></el-result></div>
  </div>
</template>

<style scoped>
.page-heading { margin-bottom: 18px; }
.page-heading h1 { font-size: 26px; color: #1f2d3d; margin: 0 0 6px; }
.page-heading p { color: #93a0b4; font-size: 13px; margin: 0; }

/* 引导页 */
.idle-card { padding: 24px 28px; }
.idle-ready {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #fef6e7;
  color: #8a6116;
  border-radius: 8px;
  padding: 9px 14px;
  font-size: 12.5px;
  margin-bottom: 20px;
}
.idle-ready.ok { background: #dcfce7; color: #16a34a; }
.ir-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.idle-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 28px; }
.idle-main h3 { margin: 0 0 8px; font-size: 17px; color: #1f2d3d; }
.idle-desc { color: #8a97ad; font-size: 13px; line-height: 1.8; margin: 0 0 18px; }
.idle-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.count-field { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #5a6b80; }
.q-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.q-chip { background: #e3effe; color: #1878fd; border-radius: 12px; font-size: 12px; padding: 3px 12px; }
.idle-side h4 { margin: 0 0 12px; font-size: 14px; color: #1f2d3d; }
.doc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  background: #f8fafc;
  border-radius: 8px;
  padding: 9px 12px;
  margin-bottom: 8px;
}
.doc-name { font-size: 12.5px; color: #3b4a5f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-list { display: flex; flex-direction: column; gap: 8px; }
.session-row {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12.5px;
  color: #5a6b80;
}
.se-id { color: #1f2d3d; font-weight: 600; }
.se-score { color: #1878fd; font-weight: 600; }
.se-time { flex: 1; text-align: right; color: #a8b3c5; }

/* 答辩三栏 */
.defense-grid {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 280px;
  gap: 14px;
  align-items: start;
}
.side-card { padding: 20px; min-height: 560px; }
.main-card { padding: 22px 24px; min-height: 560px; display: flex; flex-direction: column; }
.side-title { margin: 0 0 16px; font-size: 15px; color: #1f2d3d; }

.big-counter { font-size: 15px; color: #5a6b80; margin-bottom: 18px; }
.big-counter b { font-size: 26px; color: #1878fd; font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif; margin: 0 4px; }
.turn-list { display: flex; flex-direction: column; gap: 4px; }
.turn-item { display: flex; gap: 10px; align-items: center; padding: 8px 0; }
.ti-icon {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-style: normal;
  flex-shrink: 0;
  color: #fff;
}
.turn-item.good .ti-icon { background: #22c55e; }
.turn-item.bad .ti-icon { background: #f1f5f9; color: #e74c3c; }
.turn-item.current .ti-icon { background: #1878fd; }
.ti-body { display: flex; flex-direction: column; line-height: 1.4; min-width: 0; }
.ti-name { font-size: 13px; color: #1f2d3d; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ti-body small { font-size: 11px; color: #a8b3c5; }

/* 中央题目卡 */
.question-card {
  background: #e9f1fe;
  border-radius: 12px;
  padding: 18px 20px;
  margin-bottom: 18px;
}
.q-label { font-size: 12px; color: #1878fd; font-weight: 600; }
.q-text { margin: 10px 0 8px; font-size: 17px; color: #1f2d3d; line-height: 1.6; }
.q-hint { margin: 0; font-size: 12px; color: #7d95c0; }
.answer-input :deep(textarea) { border-radius: 10px; }
.main-actions { display: flex; justify-content: flex-end; margin-top: 14px; }
.fb-heading { margin: 6px 0 10px; font-size: 14px; color: #1f2d3d; }
.fb-card { background: #eaf8f0; border-radius: 12px; padding: 16px 18px; flex: 1; overflow-y: auto; }
.fb-label { font-size: 12px; color: #6b9a80; margin-bottom: 6px; }
.fb-answer { font-size: 13.5px; color: #1f2d3d; line-height: 1.7; white-space: pre-wrap; margin: 0 0 14px; }
.fb-feedback { font-size: 13px; color: #2f4f3f; line-height: 1.7; }

/* 右栏评价 */
.dim-row { margin-bottom: 16px; }
.dim-head { display: flex; justify-content: space-between; font-size: 12.5px; color: #5a6b80; margin-bottom: 6px; }
.dim-head b { color: #1f2d3d; }
.total-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px solid #eef2f8;
  padding-top: 14px;
  font-size: 13px;
  color: #5a6b80;
}
.total-row b { font-size: 24px; }
.src-heading { margin: 20px 0 10px; font-size: 14px; color: #1f2d3d; }
.src-row { font-size: 12px; color: #8a97ad; line-height: 2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 1200px) {
  .defense-grid { grid-template-columns: 1fr; }
  .side-card, .main-card { min-height: 0; }
  .idle-grid { grid-template-columns: 1fr; }
}
</style>
