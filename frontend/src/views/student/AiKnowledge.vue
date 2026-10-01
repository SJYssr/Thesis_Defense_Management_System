<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { aiApi } from '@/api/common'
import StatusTag from '@/components/StatusTag.vue'
const docs = ref<any[]>([])
const busy = ref(false)
let timer: ReturnType<typeof setInterval> | undefined
async function load() { docs.value = (await aiApi.listDocuments()).data }
async function index(id?: number) {
  busy.value = true
  try { if (id != null) await aiApi.indexDocument(id); else await aiApi.indexReports(); ElMessage.success('已提交索引任务'); await load() } finally { busy.value = false }
}
onMounted(() => { load(); timer = setInterval(() => { if (docs.value.some(d => ['WAITING','PROCESSING'].includes(d.index_status))) load() }, 5000) })
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <header class="student-heading"><h1>AI 知识准备</h1><p>查看论文与阶段材料的知识索引，完成后即可开始答辩练习</p></header>
  <section class="page-card knowledge-panel">
    <h3 class="block-title">知识来源</h3>
    <el-table :data="docs">
      <el-table-column prop="document_name" label="文档" min-width="220" />
      <el-table-column prop="document_type" label="类型" width="120" />
      <el-table-column prop="version" label="版本" width="80" />
      <el-table-column prop="chunk_count" label="分块数" width="90" />
      <el-table-column label="索引状态" width="110"><template #default="{ row }"><StatusTag :status="row.index_status" /></template></el-table-column>
      <el-table-column label="最近更新" width="165"><template #default="{ row }">{{ (row.indexed_at || row.created_at || '—').replace('T',' ').slice(0,16) }}</template></el-table-column>
      <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" :loading="busy" @click="index(row.id)">重建索引</el-button></template></el-table-column>
    </el-table>
    <el-alert v-for="d in docs.filter(d => d.error_message)" :key="d.id" type="error" :title="`${d.document_name}：${d.error_message}`" :closable="false" style="margin-top:12px" />
    <div class="knowledge-info"><h3 class="block-title">知识检索范围</h3><div class="knowledge-grid"><div><span>个人知识库</span><strong>仅检索当前学生材料</strong></div><div><span>内容来源</span><strong>论文 / 阶段报告 / 选题信息</strong></div><div><span>索引完成</span><strong>{{ docs.filter(d => d.index_status === 'SUCCESS').length }} / {{ docs.length }} 份</strong></div></div></div>
    <div class="knowledge-actions"><el-button @click="$router.push('/student/material')">上传材料</el-button><el-button type="primary" :loading="busy" @click="index()">开始构建索引</el-button></div>
  </section>
</template>
<style scoped>
.knowledge-panel { min-height: 650px; }.knowledge-info { margin-top: 55px; }.knowledge-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }.knowledge-grid > div { background:#f8f9fc; padding:16px; border-radius:5px; }.knowledge-grid span { display:block; color:#9aa2b0; font-size:12px; margin-bottom:8px; }.knowledge-grid strong { font-size:13px; font-weight:500; }.knowledge-actions { display:flex; justify-content:flex-end; margin-top:35px; }
@media(max-width:800px) { .knowledge-grid { grid-template-columns:1fr; } }
</style>
