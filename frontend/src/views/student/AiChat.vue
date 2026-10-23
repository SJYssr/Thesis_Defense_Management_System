<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
import { SESSION_EXPIRED_MESSAGE, authorizedFetch } from '@/api/sse'
import { healthApi } from '@/api/common'
import {
  aiAvailabilityNotice,
  citationLine,
  stepKindMeta,
  stepText,
  truncate,
  type AiNotice,
} from '@/utils/aiService'
import type { AiChatStep, AiCitationSource, AiCitationsPayload, AiChatDonePayload, AiHealthSnapshot } from '@/types/api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  /** 旧单轮路径: meta 事件里的 chunk 引用(保持原样显示, 不与 citations 重复) */
  sources?: AiCitationSource[]
  streaming?: boolean
  /** Agent 多步路径: step 事件按到达顺序累积; 为空表示后端走了单轮, 不渲染思考区块 */
  steps: AiChatStep[]
  /** citations 事件; checked=false 表示核验没跑, 此时不显示重写/移除提示 */
  citations: AiCitationsPayload | null
  /** done.degraded === true: 降级作答(未经过多步检索) */
  degraded: boolean
  /** el-collapse 的展开项: 默认空数组 = 默认折叠 */
  openSteps: string[]
}

const messages = ref<Message[]>([])
const input = ref('')
const sending = ref(false)
const listRef = ref<HTMLElement>()
/** 多步检索开关: 默认开启, 关闭时请求体 agent=false 走单轮老路径 */
const agentEnabled = ref(true)
const aiNotice = ref<AiNotice | null>(null)

const hasMessages = computed(() => messages.value.length > 0)

function emptyAssistant(): Message {
  return {
    role: 'assistant',
    content: '',
    streaming: true,
    steps: [],
    citations: null,
    degraded: false,
    openSteps: [],
  }
}

/** 折叠标题: 运行中给当前步数, 结束后给总步数(降级作答额外标注) */
function stepTitle(m: Message): string {
  const count = m.steps.length
  if (m.streaming) return count > 0 ? `思考中… (第 ${count} 步)` : '思考中…'
  const suffix = m.degraded ? '(降级作答)' : ''
  return `已完成 ${count} 步${suffix}`
}

/** 一步的副标题: 工具名 + 耗时; 没有工具名就只给耗时 */
function stepMetaText(step: AiChatStep): string {
  const parts: string[] = []
  if (step.tool) parts.push(step.tool)
  const ms = Number(step.elapsed_ms)
  if (Number.isFinite(ms) && ms >= 0) parts.push(`${ms} ms`)
  return parts.join(' · ')
}

/** observe 步额外展示观察结果前 200 字 */
function observationOf(step: AiChatStep): string {
  if (step.kind !== 'observe') return ''
  return truncate(step.observation, 200)
}

async function send() {
  const q = input.value.trim()
  if (!q || sending.value) return
  input.value = ''
  const history = messages.value
    .filter((m) => !m.streaming && m.role)
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.content }))

  messages.value.push({ role: 'user', content: q, steps: [], citations: null, degraded: false, openSteps: [] })
  const assistantMsg = emptyAssistant()
  messages.value.push(assistantMsg)
  sending.value = true
  scrollToBottom()

  try {
    // 流式接口走不了 axios 拦截器，用 authorizedFetch 复用同一套 token 过期/401 恢复策略
    const resp = await authorizedFetch('/api/v1/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // agent 为可选字段: true 让后端走多步(规划-查询-观察-作答), false 走原单轮
      body: JSON.stringify({ query: q, history, agent: agentEnabled.value }),
    })
    if (!resp.ok || !resp.body) {
      throw new Error(`HTTP ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''
      for (const evt of events) {
        const lines = evt.split('\n')
        let eventType = ''
        let dataStr = ''
        for (const line of lines) {
          if (line.startsWith('event:')) eventType = line.slice(6).trim()
          else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
        }
        if (!eventType) continue
        let data: any
        try {
          data = JSON.parse(dataStr)
        } catch {
          continue // 忽略不完整分片
        }
        handleEvent(assistantMsg, eventType, data)
      }
      scrollToBottom()
    }
    assistantMsg.streaming = false
  } catch (e: any) {
    // 会话失效时 session 侧已弹提示并跳转 /login，这里只把状态体现在气泡里
    assistantMsg.content +=
      e?.message === SESSION_EXPIRED_MESSAGE
        ? `\n\n> ⚠️ ${SESSION_EXPIRED_MESSAGE}`
        : `\n\n> ⚠️ 请求失败: ${e.message}`
    assistantMsg.streaming = false
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

/**
 * 单个 SSE 事件 -> 消息状态。
 * 未知事件名一律忽略: 后端将来加事件(或中间网关插入心跳)时前端不能整个聊天崩掉。
 */
function handleEvent(msg: Message, eventType: string, data: any) {
  switch (eventType) {
    case 'meta': {
      // 旧路径把 chunk 引用放在 meta.sources; 新路径 step/citations 另有来源
      const list = Array.isArray(data?.sources) ? data.sources : []
      msg.sources = list
      return
    }
    case 'step': {
      if (!data || typeof data !== 'object') return
      msg.steps.push({
        index: Number(data.index ?? msg.steps.length),
        kind: String(data.kind ?? ''),
        thought: typeof data.thought === 'string' ? data.thought : '',
        tool: typeof data.tool === 'string' ? data.tool : null,
        args: data.args ?? null,
        observation: typeof data.observation === 'string' ? data.observation : null,
        elapsed_ms: Number(data.elapsed_ms ?? 0),
      })
      scrollToBottom()
      return
    }
    case 'delta': {
      msg.content += typeof data === 'string' ? data : String(data?.content ?? '')
      scrollToBottom()
      return
    }
    case 'citations': {
      if (!data || typeof data !== 'object') return
      msg.citations = {
        sources: Array.isArray(data.sources) ? data.sources : [],
        checked: data.checked === true,
        regenerated: data.regenerated === true,
        invalid: Array.isArray(data.invalid) ? data.invalid : [],
      }
      return
    }
    case 'error': {
      const detail = typeof data === 'string' ? data : String(data?.message ?? '未知错误')
      msg.content += `\n\n> ⚠️ ${detail}`
      return
    }
    case 'done': {
      const payload = (data && typeof data === 'object' ? data : {}) as AiChatDonePayload
      msg.degraded = payload.degraded === true
      msg.streaming = false
      return
    }
    default:
      // 未知事件(心跳 / 未来新增类型): 忽略, 不抛错
      return
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  })
}

/** 新路径来源(citations)优先; 没有 citations 时回落到旧路径 meta.sources */
function citationSources(m: Message): AiCitationSource[] {
  const fromCitations = m.citations?.sources
  if (fromCitations && fromCitations.length) return fromCitations
  return m.sources ?? []
}

/** checked=false 表示核验未执行: 只有 checked 为 true 时才显示重写/移除提示 */
function verificationVisible(m: Message): boolean {
  return m.citations?.checked === true
}

onMounted(async () => {
  try {
    const snapshot = (await healthApi.ai()) as unknown as AiHealthSnapshot
    aiNotice.value = aiAvailabilityNotice(snapshot)
  } catch {
    // 探针不可用时不打扰问答: 真正的不可用会由 /ai/chat/stream 的错误事件体现
    aiNotice.value = null
  }
})

const suggestions = [
  '我的系统为什么选择 Redis 做向量存储?',
  '我的毕业设计有哪些创新点?',
  '答辩时如何介绍系统架构?',
]
</script>

<template>
  <div class="page-card" style="height: calc(100vh - 120px); display: flex; flex-direction: column">
    <div class="page-header">
      <h3 class="page-title">AI 知识问答</h3>
      <div class="header-right">
        <span class="header-hint">基于 RAG: 检索您的论文与材料后回答, 拒绝幻觉</span>
        <el-tooltip content="关闭后只做单轮检索作答, 不展示规划与工具调用过程" placement="top">
          <span class="agent-switch">
            <el-switch v-model="agentEnabled" size="small" :disabled="sending" />
            <span class="agent-switch-label">多步检索</span>
          </span>
        </el-tooltip>
      </div>
    </div>

    <el-alert
      v-if="aiNotice"
      :type="aiNotice.type"
      :title="aiNotice.title"
      :closable="false"
      show-icon
      class="ai-notice"
    />

    <div ref="listRef" class="chat-list">
      <el-empty v-if="!hasMessages" description="向 AI 提问您的毕业设计相关问题" :image-size="90">
        <div style="display: flex; gap: 8px; flex-wrap: wrap; justify-content: center">
          <el-button v-for="s in suggestions" :key="s" size="small" @click="input = s">{{ s }}</el-button>
        </div>
      </el-empty>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">
          <template v-if="m.role === 'user'">{{ m.content }}</template>
          <template v-else>
            <!-- 思考过程: 只有真的收到 step 事件才渲染, 单轮路径不出现空区块; 默认折叠 -->
            <el-collapse v-if="m.steps.length" v-model="m.openSteps" class="step-collapse">
              <el-collapse-item name="steps">
                <template #title>
                  <span class="step-title">
                    <el-icon v-if="m.streaming" class="is-loading"><Loading /></el-icon>
                    {{ stepTitle(m) }}
                  </span>
                </template>
                <div class="step-list">
                  <div v-for="step in m.steps" :key="step.index" class="step-row" :class="{ forced: step.kind === 'forced' }">
                    <el-tag :type="stepKindMeta(step.kind).type" size="small" effect="light">
                      {{ stepKindMeta(step.kind).label }}
                    </el-tag>
                    <span v-if="step.tool" class="step-tool">{{ step.tool }}</span>
                    <span class="step-thought">{{ stepText(step) }}</span>
                    <span class="step-meta">{{ stepMetaText(step) }}</span>
                    <div v-if="observationOf(step)" class="step-observation">{{ observationOf(step) }}</div>
                  </div>
                </div>
              </el-collapse-item>
            </el-collapse>

            <MarkdownViewer :content="m.content || '...'" />
            <span v-if="m.streaming" class="cursor">▍</span>

            <!-- 引用来源: 编号 + 类型 + section + 摘要 -->
            <div v-if="!m.streaming && citationSources(m).length" class="citations">
              <div class="citations-head">引用来源</div>
              <div v-for="(s, idx) in citationSources(m)" :key="idx" class="citation-row">
                <span class="citation-no">[{{ idx + 1 }}]</span>
                <span class="citation-meta">{{ citationLine(s).meta }}</span>
                <span class="citation-body">{{ citationLine(s).body }}</span>
              </div>
            </div>

            <!-- citations.checked=false 表示核验未执行: 不显示"重写/移除"提示, 但也不许静默 -->
            <div v-if="!m.streaming && m.citations && !m.citations.checked" class="unverified-hint">
              引用未核验: 本次回答未执行引用一致性校验
            </div>

            <!-- 核验提示: checked=false(未核验)时不显示 -->
            <el-alert
              v-if="!m.streaming && verificationVisible(m) && m.citations?.regenerated"
              type="success"
              :closable="false"
              show-icon
              class="verify-alert"
              title="回答已按引用核验重写"
            />
            <el-alert
              v-if="!m.streaming && verificationVisible(m) && m.citations && m.citations.invalid.length"
              type="warning"
              :closable="false"
              show-icon
              class="verify-alert"
              :title="`已移除无法核验的引用 [${m.citations.invalid.join(', ')}]`"
            />

            <div v-if="!m.streaming && m.degraded" class="degraded-hint">降级作答: 未经过多步检索</div>
          </template>
        </div>
      </div>
    </div>

    <div class="input-bar">
      <el-input v-model="input" :disabled="sending" placeholder="输入问题, Enter 发送" size="large"
                @keyup.enter="send" />
      <el-button type="primary" size="large" :loading="sending" @click="send">发送</el-button>
    </div>
  </div>
</template>

<style scoped>
.page-header {
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.header-hint {
  color: #909399;
  font-size: 13px;
}

.agent-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.agent-switch-label {
  color: #606266;
  font-size: 13px;
}

.ai-notice {
  margin: 8px 0 4px;
}

.chat-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px;
}

.msg {
  display: flex;
  margin-bottom: 14px;
}

.msg.user {
  justify-content: flex-end;
}

.bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
}

.msg.user .bubble {
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 2px;
  white-space: pre-wrap;
}

.msg.assistant .bubble {
  background: #f4f4f5;
  border-bottom-left-radius: 2px;
}

.step-collapse {
  margin-bottom: 10px;
  border-top: none;
  border-bottom: 1px solid #e4e7ed;
}

.step-collapse :deep(.el-collapse-item__header) {
  background: transparent;
  border-bottom: none;
  height: 32px;
  font-size: 13px;
  color: #606266;
}

.step-collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: none;
}

.step-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 8px;
}

.step-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.step-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #606266;
}

.step-row.forced .step-thought {
  color: #e6a23c;
}

.step-tool {
  color: #409eff;
  font-family: ui-monospace, Menlo, monospace;
}

.step-thought {
  flex: 1;
  min-width: 160px;
  word-break: break-word;
}

.step-meta {
  color: #a8abb2;
}

.step-observation {
  flex-basis: 100%;
  margin: 2px 0 0 0;
  padding: 4px 8px;
  background: #fff;
  border-left: 2px solid #c0c4cc;
  color: #909399;
  white-space: pre-wrap;
  word-break: break-word;
}

.cursor {
  animation: blink 0.8s infinite;
  color: #409eff;
}

@keyframes blink {
  50% { opacity: 0; }
}

.citations {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #dcdfe6;
}

.citations-head {
  color: #909399;
  font-size: 12px;
  margin-bottom: 4px;
}

.citation-row {
  display: flex;
  gap: 6px;
  font-size: 12px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 2px;
}

.citation-no {
  color: #909399;
}

.citation-meta {
  color: #409eff;
  white-space: nowrap;
}

.citation-body {
  flex: 1;
  word-break: break-word;
}

.unverified-hint {
  margin-top: 8px;
  font-size: 12px;
  color: #a8abb2;
}

.verify-alert {
  margin-top: 8px;
}

.degraded-hint {
  margin-top: 8px;
  font-size: 12px;
  color: #b88230;
}

.input-bar {
  display: flex;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}
</style>
