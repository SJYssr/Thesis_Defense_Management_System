<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { materialApi } from '@/api/business'
import { aiApi } from '@/api/common'
import StatusTag from '@/components/StatusTag.vue'
import ReviewHistory from '@/components/ReviewHistory.vue'

const view = ref<any>(null)
const uploading = ref(false)
const submitting = ref(false)
const polling = ref<number | undefined>()

const MATERIAL_TYPES = [
  { value: 'THESIS', label: '毕业论文', accept: '.pdf,.doc,.docx,.md,.txt', required: true },
  { value: 'DESIGN', label: '毕业设计作品', accept: '*', required: false },
  { value: 'SOURCE_CODE', label: '源代码', accept: '.zip,.rar', required: false },
  { value: 'MANUAL', label: '说明文档', accept: '.pdf,.doc,.docx,.md', required: false },
  { value: 'VIDEO', label: '演示视频', accept: '.mp4,.zip', required: false },
  { value: 'OTHER', label: '其他附件', accept: '*', required: false },
]

async function load() {
  const res = await materialApi.mine()
  view.value = res.data
  // 论文索引完成后刷新文档状态
  const docs = await aiApi.listDocuments()
  aiDocs.value = docs.data
}

const aiDocs = ref<any[]>([])

async function handleUpload(file: File, materialType: string) {
  uploading.value = true
  try {
    await materialApi.uploadFile(file, materialType)
    ElMessage.success('上传成功')
    await load()
    startPolling()
  } catch {
    /* 已提示 */
  } finally {
    uploading.value = false
  }
  return false
}

function startPolling() {
  if (polling.value) return
  polling.value = window.setInterval(async () => {
    await load()
    const hasProcessing = aiDocs.value.some((d) => d.index_status === 'WAITING' || d.index_status === 'PROCESSING')
    if (!hasProcessing && polling.value) {
      window.clearInterval(polling.value)
      polling.value = undefined
    }
  }, 3000)
}

async function reindex(docId: number) {
  await aiApi.indexDocument(docId)
  ElMessage.success('已重新提交索引')
  startPolling()
}

async function removeFile(row: any) {
  await ElMessageBox.confirm(`确定删除「${row.original_name}」吗?`, '提示', { type: 'warning' })
  await materialApi.removeFile(row.id)
  await load()
}

async function submit() {
  await ElMessageBox.confirm('提交后将进入教师审核流程, 确定提交?', '提示', { type: 'warning' })
  submitting.value = true
  try {
    await materialApi.submit()
    ElMessage.success('已提交, 等待教师审核')
    await load()
  } catch {
    /* 已提示 */
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  load()
  startPolling()
})
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">毕业材料</h3>
      <StatusTag v-if="view" :status="view.status" />
    </div>

    <el-alert v-if="view?.status === 'REJECTED'" type="error" :closable="false" style="margin-bottom: 16px"
              :title="`审核意见: ${view?.review_comment || '无'}`" />

    <el-row :gutter="12">
      <el-col v-for="t in MATERIAL_TYPES" :key="t.value" :span="8" style="margin-bottom: 12px">
        <el-card shadow="never" body-style="padding: 14px">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
            <span style="font-weight: 600; font-size: 14px">
              {{ t.label }}
              <el-tag v-if="t.required" size="small" type="danger" style="margin-left: 4px">必需</el-tag>
            </span>
            <el-upload :show-file-list="false" :accept="t.accept" :disabled="!view?.can_edit || uploading"
                       :before-upload="(file: File) => handleUpload(file, t.value)">
              <el-button size="small" type="primary" plain :disabled="!view?.can_edit" :loading="uploading">
                上传
              </el-button>
            </el-upload>
          </div>
          <div v-for="f in (view?.files || []).filter((x: any) => x.material_type === t.value)" :key="f.id"
               class="file-row">
            <el-icon><Document /></el-icon>
            <a :href="f.url" target="_blank" class="file-name">{{ f.original_name }}</a>
            <span style="color: #c0c4cc; font-size: 12px">{{ (f.size / 1024 / 1024).toFixed(2) }}MB</span>
            <el-button v-if="view?.can_edit" size="small" text type="danger" @click="removeFile(f)">删除</el-button>
          </div>
          <div v-if="!(view?.files || []).some((x: any) => x.material_type === t.value)" class="file-empty">
            暂未上传
          </div>
        </el-card>
      </el-col>
    </el-row>

    <div v-if="view?.can_edit" style="display: flex; justify-content: flex-end; margin-top: 8px">
      <el-button type="primary" :loading="submitting" @click="submit">提交审核</el-button>
    </div>
  </div>

  <el-row :gutter="16" style="margin-top: 16px">
    <el-col :span="14">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">AI 知识库状态</h3>
        <el-alert type="info" :closable="false" style="margin-bottom: 12px"
                  title="毕业论文(THESIS)上传后将自动解析、分块、向量化并进入您的专属知识库, 用于 AI 模拟答辩与问答。" />
        <el-table :data="aiDocs" border size="small">
          <el-table-column prop="document_name" label="文档" min-width="200" show-overflow-tooltip />
          <el-table-column label="索引状态" width="100">
            <template #default="{ row }">
              <StatusTag :status="row.index_status" />
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="分块数" width="80" />
          <el-table-column prop="error_message" label="错误" min-width="140" show-overflow-tooltip />
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button size="small" @click="reindex(row.id)">重建索引</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!aiDocs.length" description="上传论文后将自动建立知识库" :image-size="70" />
      </div>
    </el-col>
    <el-col :span="10">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">审核历史</h3>
        <ReviewHistory :records="view?.review_history || []" />
      </div>
    </el-col>
  </el-row>
</template>

<style scoped>
.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
  font-size: 13px;
}

.file-name {
  flex: 1;
  color: #409eff;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-empty {
  color: #c0c4cc;
  font-size: 12px;
  padding: 6px 0;
}
</style>
