<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { materialApi } from '@/api/business'
import { aiApi } from '@/api/common'
import MaterialStatus from '@/components/MaterialStatus.vue'

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
  const docs = await aiApi.listDocuments(1, 100)
  aiDocs.value = docs.data.items
}

const aiDocs = ref<any[]>([])

async function handleUpload(file: File, materialType: string) {
  uploading.value = true
  try {
    // P7-A: 上传支持 business_type/business_id 业务挂载; 配额用尽(40013)与单文件超限(40010)由请求层分别提示
    await materialApi.uploadFile(file, materialType, {
      business_type: 'MATERIAL',
      business_id: view.value?.id as number | undefined,
    })
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

onUnmounted(() => { if (polling.value) window.clearInterval(polling.value) })

onMounted(() => {
  load()
  startPolling()
})
</script>

<template>
  <header class="student-heading"><h1>毕业材料提交</h1><p>上传毕业设计相关文档，完成最终材料归档</p></header>
  <div class="student-split">
    <section class="page-card student-form">
      <h3 class="block-title">材料信息</h3>
      <div v-for="t in MATERIAL_TYPES" :key="t.value" class="upload-field">
        <label>{{ t.label }}<span v-if="t.required" class="required"> *</span></label>
        <el-upload :show-file-list="false" :accept="t.accept" :disabled="view?.can_edit === false || uploading" :before-upload="(file: File) => handleUpload(file, t.value)">
          <button type="button" class="upload-control" :disabled="view?.can_edit === false || uploading"><el-icon><Upload /></el-icon> {{ uploading ? '正在上传…' : `点击上传${t.label}文件` }}</button>
        </el-upload>
        <div v-for="f in (view?.files || []).filter((x: any) => x.material_type === t.value)" :key="f.id" class="file-row">
          <el-icon><Document /></el-icon><a :href="f.url" target="_blank" rel="noopener" class="file-name">{{ f.original_name }}</a>
          <small>{{ (f.size / 1024 / 1024).toFixed(2) }}MB</small>
          <el-button v-if="view?.can_edit" link type="danger" @click="removeFile(f)">删除</el-button>
        </div>
      </div>
      <div class="form-actions"><el-button @click="$router.push('/student/ai-knowledge')">查看知识准备</el-button><el-button type="primary" :disabled="!view?.can_edit" :loading="submitting" @click="submit">提交</el-button></div>
    </section>
    <MaterialStatus :view="view" :history="view?.review_history || []" />
  </div>
</template>
<style scoped>
.upload-field { margin-bottom: 23px; }
.upload-field label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 10px; }
.required { color: #ff4965; }
.upload-field :deep(.el-upload) { width: 100%; }
.upload-control { width: 100%; text-align: left; border: 1px solid #e8ebf0; background: #f9fafc; color: #98a1b0; padding: 13px; border-radius: 5px; cursor: pointer; display: flex; gap: 8px; align-items: center; }
.upload-control:hover { border-color: #087cff; color: #087cff; }
.upload-control:disabled { cursor: not-allowed; opacity: .65; }
.file-row { display: flex; gap: 8px; align-items: center; padding: 8px 0; font-size: 12px; }
.file-name { color: #087cff; flex: 1; overflow: hidden; text-overflow: ellipsis; }
.file-row small { color: #98a1b0; }
</style>
