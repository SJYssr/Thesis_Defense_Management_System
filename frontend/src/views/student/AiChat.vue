<script setup lang="ts">
import { nextTick, ref } from 'vue'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
import { SESSION_EXPIRED_MESSAGE, authorizedFetch } from '@/api/sse'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: any[]
  streaming?: boolean
}

const messages = ref<Message[]>([])
const input = ref('')
const sending = ref(false)
const listRef = ref<HTMLElement>()

async function send() {
  const q = input.value.trim()
  if (!q || sending.value) return
  input.value = ''
  const history = messages.value
    .filter((m) => !m.streaming)
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.content }))

  messages.value.push({ role: 'user', content: q })
  const assistantMsg: Message = { role: 'assistant', content: '', streaming: true }
  messages.value.push(assistantMsg)
  sending.value = true
  scrollToBottom()

  try {
    // 流式接口走不了 axios 拦截器，用 authorizedFetch 复用同一套 token 过期/401 恢复策略
    const resp = await authorizedFetch('/api/v1/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, history }),
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
        try {
          const data = JSON.parse(dataStr)
          if (eventType === 'meta') {
            assistantMsg.sources = data.sources || []
          } else if (eventType === 'delta') {
            assistantMsg.content += data
            scrollToBottom()
          } else if (eventType === 'done') {
            assistantMsg.streaming = false
          }
        } catch {
          /* 忽略不完整分片 */
        }
      }
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

function scrollToBottom() {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  })
}

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
      <span style="color: #909399; font-size: 13px">基于 RAG: 检索您的论文与材料后回答, 拒绝幻觉</span>
    </div>

    <div ref="listRef" class="chat-list">
      <el-empty v-if="!messages.length" description="向 AI 提问您的毕业设计相关问题" :image-size="90">
        <div style="display: flex; gap: 8px; flex-wrap: wrap; justify-content: center">
          <el-button v-for="s in suggestions" :key="s" size="small" @click="input = s">{{ s }}</el-button>
        </div>
      </el-empty>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">
          <template v-if="m.role === 'user'">{{ m.content }}</template>
          <template v-else>
            <MarkdownViewer :content="m.content || '...'" />
            <span v-if="m.streaming" class="cursor">▍</span>
            <div v-if="m.sources && m.sources.length && !m.streaming" class="sources">
              <span style="color: #909399; font-size: 12px">引用: </span>
              <el-tag v-for="s in m.sources.slice(0, 4)" :key="s.chunk_id" size="small" type="info" style="margin-right: 4px">
                {{ s.section || s.document_type }}
              </el-tag>
            </div>
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

.cursor {
  animation: blink 0.8s infinite;
  color: #409eff;
}

@keyframes blink {
  50% { opacity: 0; }
}

.sources {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #dcdfe6;
}

.input-bar {
  display: flex;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}
</style>
