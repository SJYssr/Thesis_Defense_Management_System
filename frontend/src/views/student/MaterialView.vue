<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { materialApi } from '@/api/business'
import { aiApi, uploadApi } from '@/api/common'
import type { UploadKind, UploadPolicy } from '@/types/api'
import MaterialStatus from '@/components/MaterialStatus.vue'

const view = ref<any>(null)
const uploading = ref(false)
const submitting = ref(false)
const polling = ref<number | undefined>()

const policy = ref<UploadPolicy | null>(null)

/**
 * 各类材料允许上传的扩展名分组(不是扩展名清单本身)。
 * 具体扩展名由后端 /files/upload-policy 下发, 前端不另立清单:
 * 原先这里写死 accept='.mp4,.zip' 而后端白名单没有 .mp4, 演示视频必然被拒。
 */
const MATERIAL_TYPES: { value: string; label: string; kinds: UploadKind[]; required?: boolean }[] = [
  { value: 'THESIS', label: '毕业论文', kinds: ['DOCUMENT'], required: true },
  { value: 'DESIGN', label: '毕业设计作品', kinds: ['DOCUMENT', 'ARCHIVE', 'IMAGE', 'MEDIA'] },
  { value: 'SOURCE_CODE', label: '源代码', kinds: ['ARCHIVE'] },
  { value: 'MANUAL', label: '说明文档', kinds: ['DOCUMENT', 'IMAGE'] },
  { value: 'VIDEO', label: '演示视频', kinds: ['MEDIA', 'ARCHIVE'] },
  { value: 'OTHER', label: '其他附件', kinds: ['DOCUMENT', 'ARCHIVE', 'IMAGE', 'MEDIA'] },
]

/** 由策略生成 accept; 策略未就绪时返回 undefined(交给后端兜底), 不要写成 '*' */
function acceptOf(t: { kinds: UploadKind[] }): string | undefined {
  const p = policy.value
  if (!p) return undefined
  const exts = t.kinds.flatMap((k) => p.by_kind[k] ?? [])
  return exts.length ? exts.join(',') : undefined
}

const policyHint = computed(() => {
  const p = policy.value
  if (!p) return ''
  const total = Object.values(p.by_kind).reduce((n, list) => n + list.length, 0)
  const parts = ['支持 ' + total + ' 种格式(文档/压缩包/图片/音视频)']
  if (p.max_upload_size_mb > 0) parts.push('单文件 ≤ ' + p.max_upload_size_mb + 'MB')
  if (p.max_upload_daily_mb > 0) parts.push('每日合计 ≤ ' + p.max_upload_daily_mb + 'MB')
  return parts.join(', ')
})

function extensionOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

/** 上传前预校验: 扩展名与大小先本地拦一遍, 给出具体原因而不是等后端 40010 一行提示 */
function precheck(file: File): string | null {
  const p = policy.value
  if (!p) return null
  const ext = extensionOf(file.name)
  if (!ext || !p.allowed_extensions.includes(ext)) {
    return '不支持的文件类型: ' + (ext || '(无扩展名)')
  }
  if (p.max_upload_size_mb > 0 && file.size > p.max_upload_size_mb * 1024 * 1024) {
    return '文件超过 ' + p.max_upload_size_mb + 'MB 限制(当前 ' + (file.size / 1024 / 1024).toFixed(2) + 'MB)'
  }
  return null
}

async function loadPolicy() {
  try {
    policy.value = (await uploadApi.policy()).data
  } catch {
    policy.value = null
  }
}

async function load() {
  const res = await materialApi.mine()
  view.value = res.data
  // 论文索引完成后刷新文档状态
  const docs = await aiApi.listDocuments(1, 100)
  aiDocs.value = docs.data.items
}

const aiDocs = ref<any[]>([])

async function handleUpload(file: File, materialType: string) {
  const blocked = precheck(file)
  if (blocked) {
    ElMessage.error(blocked)
    return false
  }
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
  loadPolicy()
  load()
  startPolling()
})
</script>

<template>
  <header class="student-heading"><h1>毕业材料提交</h1><p>上传毕业设计相关文档，完成最终材料归档</p></header>
  <div class="student-split">
    <section class="page-card student-form">
      <h3 class="block-title">材料信息</h3>
      <p v-if="policyHint" class="upload-hint">{{ policyHint }}</p>
      <div v-for="t in MATERIAL_TYPES" :key="t.value" class="upload-field">
        <label>{{ t.label }}<span v-if="t.required" class="required"> *</span></label>
        <el-upload :show-file-list="false" :accept="acceptOf(t)" :disabled="view?.can_edit === false || uploading" :before-upload="(file: File) => handleUpload(file, t.value)">
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
.upload-hint { margin: -6px 0 16px; font-size: 12px; color: #98a1b0; }
.upload-field :deep(.el-upload) { width: 100%; }
.upload-control { width: 100%; text-align: left; border: 1px solid #e8ebf0; background: #f9fafc; color: #98a1b0; padding: 13px; border-radius: 5px; cursor: pointer; display: flex; gap: 8px; align-items: center; }
.upload-control:hover { border-color: #087cff; color: #087cff; }
.upload-control:disabled { cursor: not-allowed; opacity: .65; }
.file-row { display: flex; gap: 8px; align-items: center; padding: 8px 0; font-size: 12px; }
.file-name { color: #087cff; flex: 1; overflow: hidden; text-overflow: ellipsis; }
.file-row small { color: #98a1b0; }
</style>
