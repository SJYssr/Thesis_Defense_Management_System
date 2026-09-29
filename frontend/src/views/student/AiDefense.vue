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
  const res = await aiApi.listDocuments()
  docs.value = res.data
  docReady.value = res.data.some((d: any) => d.index_status === 'SUCCESS')
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
const pptOutline = ref<any>(null)
const sessions = ref<any[]>([])
const generatingPpt = ref(false)

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
    await loadSessions()
    ElMessage.success('答辩结束, 报告已生成')
  } else {
    currentQuestion.value = data
    phase.value = 'answering'
  }
}

async function viewReport(sid: number) {
  const res = await aiApi.report(sid)
  report.value = res.data
  sessionId.value = sid
  phase.value = 'finished'
}

async function generatePpt() {
  generatingPpt.value = true
  try {
    const res = await aiApi.pptOutline()
    pptOutline.value = res.data
  } catch {
    /* 已提示 */
  } finally {
    generatingPpt.value = false
  }
}

const progressPercent = computed(() => {
  if (!report.value) {
    const total = questionCount.value
    return Math.min(Math.round((turns.length / total) * 100), 100)
  }
  return 100
})

const scoreDimensions = computed(() => {
  if (!evaluation.value) return []
  const s = evaluation.value.scores
  return [
    { name: '知识准确性', value: s.accuracy, max: 35 },
    { name: '逻辑完整性', value: s.logic, max: 25 },
    { name: '表达清晰度', value: s.clarity, max: 20 },
    { name: '技术深度', value: s.depth, max: 20 },
  ]
})

onMounted(async () => {
  await checkDocs()
  await loadSessions()
})
</script>

<template>
  <!-- 未开始: 引导 -->
  <div v-if="phase === 'idle'" class="page-card">
    <div class="page-header">
      <h3 class="page-title">AI 模拟答辩</h3>
      <el-button @click="router.push('/student/ai-chat')">知识问答</el-button>
    </div>

    <el-alert :type="docReady ? 'success' : 'warning'" :closable="false" style="margin-bottom: 16px"
              :title="docReady ? '知识库就绪: 基于您的论文与材料生成针对性答辩问题' : '知识库为空: 请先在「毕业材料」页上传毕业论文, 等待索引完成后即可开始 AI 模拟答辩'" />

    <div v-if="docs.length" style="margin-bottom: 16px">
      <el-table :data="docs" border size="small">
        <el-table-column prop="document_name" label="知识文档" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><StatusTag :status="row.index_status" /></template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="分块" width="70" />
      </el-table>
    </div>

    <div style="display: flex; gap: 12px; align-items: center">
      <el-input-number v-model="questionCount" :min="3" :max="22" />
      <span style="color: #909399; font-size: 13px">题数</span>
      <el-button @click="generateQuestions" :loading="generating" :disabled="!docReady">生成问题库</el-button>
      <el-button type="primary" size="large" @click="startDefense" :disabled="!docReady">开始模拟答辩</el-button>
    </div>

    <div v-if="questions.length" style="margin-top: 16px">
      <el-divider>已生成 {{ questions.length }} 个问题(按维度)</el-divider>
      <el-tag v-for="(q, i) in questions" :key="i" style="margin: 4px" :type="q.question_type === 'TECHNOLOGY' ? 'success' : q.question_type === 'DESIGN' ? 'warning' : 'info'">
        {{ TYPE_CN[q.question_type] || q.question_type }}
      </el-tag>
    </div>

    <el-divider>历史会话</el-divider>
    <el-table v-if="sessions.length" :data="sessions" border size="small">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column prop="answered_count" label="已答" width="70" />
      <el-table-column prop="overall_score" label="综合得分" width="90" />
      <el-table-column prop="finished_at" label="结束时间" width="170">
        <template #default="{ row }">{{ (row.finished_at || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button v-if="row.status === 'FINISHED'" size="small" type="primary" plain @click="viewReport(row.id)">查看报告</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="暂无历史会话" :image-size="70" />
  </div>

  <!-- 答辩进行中 -->
  <el-row v-else-if="phase === 'answering' || phase === 'feedback'" :gutter="16">
    <el-col :span="6">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">答辩进度</h3>
        <el-progress :percentage="progressPercent" :stroke-width="10" />
        <div style="text-align: center; margin: 12px 0; font-size: 15px">
          第 {{ turns.length + (phase === 'answering' ? 1 : 0) }} 轮
        </div>
        <div v-for="(t, i) in turns" :key="i" class="turn-chip">
          <el-icon :color="t.scores.total >= 60 ? '#67c23a' : '#f56c6c'">
            <CircleCheckFilled v-if="t.scores.total >= 60" /><CircleCloseFilled v-else />
          </el-icon>
          第{{ i + 1 }}轮 · {{ t.scores.total }}分
        </div>
      </div>
    </el-col>

    <el-col :span="12">
      <div class="page-card" style="min-height: 420px; display: flex; flex-direction: column">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
          <el-tag type="primary">
            {{ currentQuestion?.is_follow_up ? '追问' : TYPE_CN[currentQuestion?.question_type] || '问题' }}
          </el-tag>
          <span style="color: #909399; font-size: 13px">AI 答辩教师</span>
        </div>
        <div class="question-box">{{ currentQuestion?.question }}</div>

        <template v-if="phase === 'answering'">
          <el-input v-model="answer" type="textarea" :rows="7" style="margin-top: 16px"
                    placeholder="请输入您的回答, 建议结合论文内容展开..." />
          <div style="margin-top: 12px; text-align: right">
            <el-button type="primary" :loading="evaluating" @click="submitAnswer">
              {{ evaluating ? 'AI 评价中...' : '提交回答' }}
            </el-button>
          </div>
        </template>

        <template v-else>
          <div class="feedback-box">
            <div class="fb-label">您的回答</div>
            <div class="fb-content">{{ turns[turns.length - 1]?.answer }}</div>
            <div class="fb-label" style="margin-top: 12px">AI 评价</div>
            <div class="fb-content markdown-body">
              <MarkdownViewer :content="evaluation?.feedback || ''" />
            </div>
            <div v-if="evaluation?.sources?.length" class="fb-label" style="margin-top: 12px">
              评价依据(论文检索)
            </div>
            <div v-for="s in evaluation?.sources || []" :key="s.chunk_id" class="source-chip">
              {{ s.section || s.document_type }} · chunk#{{ s.chunk_id }}
            </div>
          </div>
          <div style="margin-top: 12px; text-align: right">
            <el-button type="primary" @click="nextQuestion">下一题</el-button>
          </div>
        </template>
      </div>
    </el-col>

    <el-col :span="8">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">当前评分</h3>
        <template v-if="evaluation">
          <div v-for="d in scoreDimensions" :key="d.name" style="margin-bottom: 12px">
            <div style="display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px">
              <span>{{ d.name }}</span><span>{{ d.value }}/{{ d.max }}</span>
            </div>
            <el-progress :percentage="Math.round((d.value / d.max) * 100)" :stroke-width="8"
                         :color="d.value / d.max >= 0.6 ? '#67c23a' : '#e6a23c'" />
          </div>
          <el-divider />
          <div style="text-align: center">
            <div style="font-size: 28px; font-weight: 700" :style="{ color: (evaluation.scores.total >= 60 ? '#67c23a' : '#f56c6c') }">
              {{ evaluation.scores.total }}
            </div>
            <div style="color: #909399; font-size: 12px">本轮总分</div>
          </div>
        </template>
        <el-empty v-else description="提交回答后显示评分" :image-size="70" />
      </div>
    </el-col>
  </el-row>

  <!-- 答辩结束: 报告 -->
  <div v-else-if="phase === 'finished' && report" class="page-card">
    <div class="page-header">
      <h3 class="page-title">答辩报告</h3>
      <div style="display: flex; gap: 8px">
        <el-button @click="generatePpt" :loading="generatingPpt">生成 PPT 大纲</el-button>
        <el-button type="primary" @click="phase = 'idle'; report = null">返回</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :span="6" style="text-align: center">
        <el-progress type="dashboard" :percentage="Math.min(report.overall_score, 100)" :width="140"
                     :color="report.overall_score >= 60 ? '#67c23a' : '#f56c6c'" />
        <div style="font-size: 16px; margin-top: 8px">综合得分: {{ report.overall_score }}</div>
      </el-col>
      <el-col :span="18">
        <p style="color: #606266; line-height: 1.8">{{ report.summary }}</p>
        <el-row :gutter="12">
          <el-col v-for="(v, k) in report.dimension_scores" :key="k" :span="4">
            <div class="dim-card">
              <div class="dim-value">{{ v }}</div>
              <div class="dim-label">{{ k }}</div>
            </div>
          </el-col>
        </el-row>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="8">
        <h4>✅ 优势</h4>
        <ul><li v-for="(s, i) in report.strengths" :key="i">{{ s }}</li></ul>
      </el-col>
      <el-col :span="8">
        <h4>⚠️ 薄弱点</h4>
        <ul><li v-for="(s, i) in report.weaknesses" :key="i">{{ s }}</li></ul>
      </el-col>
      <el-col :span="8">
        <h4>📚 复习建议</h4>
        <ul><li v-for="(s, i) in report.suggestions" :key="i">{{ s }}</li></ul>
      </el-col>
    </el-row>

    <el-divider>容易被追问的问题</el-divider>
    <el-alert v-for="(q, i) in report.risky_questions" :key="i" :title="q" type="warning" :closable="false"
              style="margin-bottom: 8px" />

    <el-divider>逐题回顾</el-divider>
    <el-collapse>
      <el-collapse-item v-for="(t, i) in report.turns" :key="i"
                        :title="`第${t.turn_no}题(${t.total_score}分): ${t.question.slice(0, 40)}${t.question.length > 40 ? '...' : ''}`">
        <div class="fb-label">问题</div>
        <div class="fb-content">{{ t.question }}</div>
        <div class="fb-label" style="margin-top: 8px">我的回答</div>
        <div class="fb-content">{{ t.student_answer }}</div>
        <div class="fb-label" style="margin-top: 8px">AI 评价</div>
        <div class="fb-content markdown-body"><MarkdownViewer :content="t.ai_feedback || ''" /></div>
      </el-collapse-item>
    </el-collapse>

    <template v-if="pptOutline">
      <el-divider>答辩 PPT 大纲(AI 生成)</el-divider>
      <h3 style="text-align: center">{{ pptOutline.title }}</h3>
      <el-row :gutter="12">
        <el-col v-for="p in pptOutline.pages" :key="p.page" :span="8" style="margin-bottom: 12px">
          <el-card shadow="hover">
            <template #header>
              <strong>P{{ p.page }} {{ p.title }}</strong>
            </template>
            <ul style="padding-left: 18px; margin: 0; font-size: 13px">
              <li v-for="(k, i) in p.key_points" :key="i">{{ k }}</li>
            </ul>
            <div style="color: #909399; font-size: 12px; margin-top: 8px">
              💬 {{ p.speaker_notes }}<br />📊 建议: {{ p.recommended_visual }}
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.question-box {
  background: #f0f7ff;
  border-left: 4px solid #409eff;
  padding: 16px;
  border-radius: 6px;
  font-size: 16px;
  line-height: 1.7;
}

.feedback-box {
  background: #fafafa;
  border-radius: 6px;
  padding: 12px;
  margin-top: 16px;
}

.fb-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.fb-content {
  font-size: 14px;
  color: #303133;
  white-space: pre-wrap;
}

.source-chip {
  display: inline-block;
  background: #f4f4f5;
  color: #909399;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  margin: 2px 6px 0 0;
}

.turn-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #606266;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
}

.dim-card {
  background: #f5f7fa;
  border-radius: 6px;
  text-align: center;
  padding: 10px 4px;
}

.dim-value {
  font-size: 20px;
  font-weight: 700;
  color: #409eff;
}

.dim-label {
  font-size: 12px;
  color: #909399;
}
</style>
