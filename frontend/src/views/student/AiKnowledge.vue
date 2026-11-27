<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { aiApi, listKnowledgeMaterials, uploadApi } from '@/api/common'
import type { PracticeMaterialKind, UploadPolicy } from '@/types/api'
import StatusTag from '@/components/StatusTag.vue'

const TYPE_CN: Record<string, string> = {
  PRACTICE_REPORT: '练习报告', SOURCE_CODE: '源代码', DESIGN: '设计作品', MANUAL: '说明文档', THESIS: '论文', OPENING_REPORT: '开题报告', MIDTERM_REPORT: '中期报告', FINAL_REPORT: '终期报告',
  TOPIC: '题目信息', TEACHER_COMMENT: '教师评语', DEFENSE_RULE: '答辩规范', REFERENCE: '参考资料',
}
const policy = ref<UploadPolicy | null>(null)
const uploading = ref<PracticeMaterialKind | null>(null)
const kinds = [{ kind: 'REPORT', label: '上传报告草稿' }, { kind: 'SOURCE_CODE', label: '上传项目源码' }] as const

async function loadPolicy() {
  try { policy.value = (await uploadApi.policy()).data } catch { policy.value = null }
}

async function upload(kind: PracticeMaterialKind, file: File) {
  if (!policy.value || uploading.value) return false
  const extensions = policy.value.practice?.[kind] || []
  const limit = policy.value.practice_max_upload_size_mb?.[kind] ?? policy.value.max_upload_size_mb
  if (!extensions.some(ext => file.name.toLowerCase().endsWith(ext.toLowerCase()))) {
    ElMessage.warning('请选择支持的文件格式：' + extensions.join('、'))
    return false
  }
  if (limit && file.size > limit * 1024 * 1024) {
    ElMessage.warning('单文件大小上限为 ' + limit + ' MB')
    return false
  }
  uploading.value = kind
  try {
    await aiApi.uploadPracticeMaterial(kind, file)
    ElMessage.success('已添加到个人练习材料库')
  } catch { /* 请求层显示上传失败原因。 */ }
  finally {
    uploading.value = null
    await load()
  }
  return false
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
  // 后端已分页(page/size ≤ 100)，知识库页需要全量做统计与刷新判断
  docs.value = await listKnowledgeMaterials()
  if (!config.value) config.value = (await aiApi.ragConfig()).data
}
async function index(id?: number) {
  if (busy.value) return
  busy.value = true
  try {
    if (id != null) {
      const res = await aiApi.indexDocument(id)
      ElMessage.success(res.data?.message || '文档索引已更新')
    } else {
      const res = await aiApi.indexReports()
      if (res.data?.failed_document_ids?.length) ElMessage.warning(res.message)
      else ElMessage.success(res.message)
    }
  } catch { /* 请求层已显示索引失败原因。 */ }
  finally {
    try { await load() } finally { busy.value = false }
  }
}
onMounted(() => {
  void loadPolicy()
  load()
  timer = setInterval(() => { if (!busy.value && docs.value.some(d => ['WAITING', 'PROCESSING'].includes(d.index_status))) load() }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <header class="student-heading"><h1>AI 知识准备</h1><p>查看毕业论文、阶段报告和源码的索引。已有材料可在此补建索引。</p></header>
  <section class="page-card knowledge-panel">
    <h3 class="block-title">答辩材料</h3>
    <p>上传本人的报告或源码后，系统会为当前毕业课题建立索引，供视频答辩自动取用。</p>
    <div class="practice-uploads">
      <el-upload v-for="item in kinds" :key="item.kind" :show-file-list="false"
                 :accept="policy?.practice?.[item.kind]?.join(',')" :disabled="!policy?.practice?.[item.kind]?.length || !!uploading"
                 :before-upload="(file: File) => upload(item.kind, file)">
        <el-button :loading="uploading === item.kind" :disabled="!policy?.practice?.[item.kind]?.length || !!uploading">{{ item.label }}</el-button>
      </el-upload>
      <el-button v-if="!policy" link type="primary" @click="loadPolicy">重新读取上传设置</el-button>
    </div>
    <el-table :data="docs">
      <el-table-column prop="document_name" label="文档" min-width="220" />
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ TYPE_CN[row.document_type] || row.document_type }}</template>
      </el-table-column>
      <el-table-column label="大小" width="90">
        <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="片段数" width="85" />
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
      <el-button :loading="busy" @click="index()">同步并索引本期材料</el-button>
      <el-button type="primary" @click="$router.push('/student/ai-defense')">开始模拟答辩</el-button>
    </div>
  </section>
</template>
<style scoped>
.practice-uploads { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 12px 0 18px; }
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
