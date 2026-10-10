<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { aiApi } from '@/api/common'
import StatusTag from '@/components/StatusTag.vue'

const TYPE_CN: Record<string, string> = {
  THESIS: '论文', OPENING_REPORT: '开题报告', MIDTERM_REPORT: '中期报告', FINAL_REPORT: '终期报告',
  TOPIC: '题目信息', TEACHER_COMMENT: '教师评语', DEFENSE_RULE: '答辩规范', REFERENCE: '参考资料',
}
const docs = ref<any[]>([])
const config = ref<any>(null)
const busy = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

const indexedCount = computed(() => docs.value.filter(d => d.index_status === 'SUCCESS').length)

function fmtSize(size: number | null | undefined) {
  if (size == null) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function load() {
  docs.value = (await aiApi.listDocuments()).data
  if (!config.value) config.value = (await aiApi.ragConfig()).data
}
async function index(id?: number) {
  busy.value = true
  try {
    if (id != null) await aiApi.indexDocument(id)
    else await aiApi.indexReports()
    ElMessage.success('已提交索引任务')
    await load()
  } finally { busy.value = false }
}
onMounted(() => {
  load()
  timer = setInterval(() => { if (docs.value.some(d => ['WAITING', 'PROCESSING'].includes(d.index_status))) load() }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <header class="student-heading"><h1>AI 知识准备</h1><p>查看论文与阶段材料的解析、切分和向量索引状态</p></header>
  <section class="page-card knowledge-panel">
    <h3 class="block-title">知识来源</h3>
    <el-table :data="docs">
      <el-table-column prop="document_name" label="文档" min-width="220" />
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ TYPE_CN[row.document_type] || row.document_type }}</template>
      </el-table-column>
      <el-table-column label="大小" width="90">
        <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="Chunk" width="85" />
      <el-table-column label="索引状态" width="110"><template #default="{ row }"><StatusTag :status="row.index_status" /></template></el-table-column>
      <el-table-column label="最近更新" width="165"><template #default="{ row }">{{ (row.indexed_at || row.created_at || '—').replace('T', ' ').slice(0, 16) }}</template></el-table-column>
      <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" :loading="busy" @click="index(row.id)">重建索引</el-button></template></el-table-column>
    </el-table>
    <el-alert v-for="d in docs.filter(d => d.error_message)" :key="d.id" type="error" :title="`${d.document_name}：${d.error_message}`" :closable="false" style="margin-top:12px" />
    <div class="knowledge-info">
      <h3 class="block-title">RAG 检索配置</h3>
      <div class="knowledge-grid">
        <div><span>个人知识范围</span><strong>{{ (config?.personal_scope || []).join(' / ') || '论文 / 阶段报告 / 选题信息' }}</strong></div>
        <div><span>全局知识范围</span><strong>{{ (config?.global_scope || []).join(' / ') || '答辩规范 / 专业知识' }}</strong></div>
        <div><span>Top K</span><strong>{{ config?.default_top_k ?? 6 }}</strong></div>
        <div><span>检索策略</span><strong>{{ config?.retrieval || '向量 + 关键词混合' }}</strong></div>
        <div><span>多轮问题重写</span><strong>{{ config?.query_rewrite || '已开启' }}</strong></div>
        <div><span>索引完成</span><strong>{{ indexedCount }} / {{ docs.length }} 份</strong></div>
      </div>
      <p class="knowledge-note">AI 功能仅用于练习与知识准备，结果不计入正式成绩；个人知识库与其它学生完全隔离。</p>
    </div>
    <div class="knowledge-actions">
      <el-button @click="$router.push('/student/material')">上传材料</el-button>
      <el-button :loading="busy" @click="index()">开始构建索引</el-button>
      <el-button type="primary" @click="$router.push('/student/ai-defense')">开始模拟答辩</el-button>
    </div>
  </section>
</template>
<style scoped>
.knowledge-panel { min-height: 650px; }
.knowledge-info { margin-top: 40px; }
.knowledge-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.knowledge-grid > div { background: #f8f9fc; padding: 16px; border-radius: 5px; }
.knowledge-grid span { display: block; color: #9aa2b0; font-size: 12px; margin-bottom: 8px; }
.knowledge-grid strong { font-size: 13px; font-weight: 500; }
.knowledge-note { color: #9aa2b0; font-size: 12px; margin-top: 18px; }
.knowledge-actions { display: flex; justify-content: flex-end; margin-top: 28px; }
@media (max-width: 800px) { .knowledge-grid { grid-template-columns: 1fr; } .knowledge-actions { flex-wrap: wrap; } }
</style>
