<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { archiveApi } from '@/api/defense'
import { uploadApi } from '@/api/common'
import type { ArchiveEligibility, ArchiveEligibleFile, ArchiveFileRole, UploadKind, UploadPolicy } from '@/types/api'

const packages = ref<any[]>([])
const loaded = ref(false)
const detailVisible = ref(false)
const detail = ref<any | null>(null)

/** P9: 学生自助归档资格; null = 接口未返回, 此时不出告警也不放行提交(避免误报) */
const eligibility = ref<ArchiveEligibility | null>(null)

const submitVisible = ref(false)
const submitting = ref(false)
const uploading = ref(false)

/** 提交表单字段(题名/摘要/关键词), 全部可选 —— 只有文件是必填 */
const form = ref<{ title: string; abstract: string; keywords: string }>({ title: '', abstract: '', keywords: '' })

/** 已勾选的文件与用途; 未勾选的不进 payload(后端 files 是 min_length=1) */
const picked = ref<{ file_id: number; file_role: ArchiveFileRole }[]>([])

/** 本次弹窗里现场上传的文件: 上传成功即并入可选列表, 与 eligibility.files 同构 */
const uploadedFiles = ref<ArchiveEligibleFile[]>([])

const policy = ref<UploadPolicy | null>(null)

/**
 * 归档文件可用的扩展名分组(不是扩展名清单本身)。
 * 具体扩展名由 GET /files/upload-policy 下发, 前端不另立清单 —— 与毕业材料提交同一口径。
 */
const ARCHIVE_KINDS: UploadKind[] = ['DOCUMENT', 'ARCHIVE', 'IMAGE', 'MEDIA']

/** file_role 是后端的强取值, 前端只做下拉, 不许自由输入 */
const FILE_ROLES: { value: ArchiveFileRole; label: string }[] = [
  { value: 'THESIS', label: '毕业论文' },
  { value: 'DESIGN', label: '设计作品' },
  { value: 'SOURCE_CODE', label: '源代码' },
  { value: 'MANUAL', label: '说明文档' },
  { value: 'VIDEO', label: '演示视频' },
  { value: 'OTHER', label: '其他附件' },
]

/** 由策略生成 accept; 策略未就绪时返回 undefined(交给后端兜底), 不能写 '*' */
function acceptOf(): string | undefined {
  const p = policy.value
  if (!p) return undefined
  const exts = ARCHIVE_KINDS.flatMap((k) => p.by_kind[k] ?? [])
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

/** 可选文件 = 该流程已上传文件 + 本次现场上传的文件(按 file_id 去重) */
const availableFiles = computed<ArchiveEligibleFile[]>(() => {
  const seen = new Set<number>()
  const out: ArchiveEligibleFile[] = []
  for (const f of [...(eligibility.value?.files ?? []), ...uploadedFiles.value]) {
    if (seen.has(f.file_id)) continue
    seen.add(f.file_id)
    out.push(f)
  }
  return out
})

/**
 * 资格通过还要看归档包现状: PENDING 重复提交会被后端拒("归档申请审核中"),
 * ARCHIVED 再提交等于重复归档 —— 这两种情况禁用按钮, 缺口由上方 el-alert 说明。
 * REVOKED 放行: 撤回后重新提交复用同一条记录(后端按 cycle_id 唯一处理)。
 */
const canSubmitArchive = computed(() => {
  const e = eligibility.value
  if (!e || e.eligible !== true) return false
  return e.package_status !== 'PENDING' && e.package_status !== 'ARCHIVED'
})

function sizeText(size?: number): string {
  if (!size) return '—'
  return (size / 1024 / 1024).toFixed(2) + 'MB'
}

function isPicked(fileId: number): boolean {
  return picked.value.some((x) => x.file_id === fileId)
}

function roleOf(fileId: number): ArchiveFileRole {
  return picked.value.find((x) => x.file_id === fileId)?.file_role ?? 'OTHER'
}

function setRole(fileId: number, role: ArchiveFileRole): void {
  const item = picked.value.find((x) => x.file_id === fileId)
  if (item) item.file_role = role
}

/** 勾选/取消; 只有一个文件时默认 THESIS(论文本身), 其余默认 OTHER */
function toggleFile(file: ArchiveEligibleFile, checked?: boolean): void {
  const next = checked === undefined ? !isPicked(file.file_id) : checked
  if (!next) {
    picked.value = picked.value.filter((x) => x.file_id !== file.file_id)
    return
  }
  if (isPicked(file.file_id)) return
  picked.value.push({ file_id: file.file_id, file_role: picked.value.length === 0 ? 'THESIS' : 'OTHER' })
}

async function loadPolicy() {
  try {
    policy.value = (await uploadApi.policy()).data
  } catch {
    policy.value = null
  }
}

async function load() {
  const res = await archiveApi.search({})
  packages.value = res.data.items || []
  loaded.value = true
}

async function loadEligibility() {
  try {
    eligibility.value = (await archiveApi.eligibility()).data
  } catch {
    // 接口异常时不放行提交, 也不弹二次提示(请求层已提示)
    eligibility.value = null
  }
}

async function openDetail(p: any) {
  const res = await archiveApi.detail(p.id)
  detail.value = res.data
  detailVisible.value = true
}

async function grant(p: any, visibility: string) {
  await archiveApi.grantConsent(p.id, visibility)
  ElMessage.success(visibility === 'FULL_TEXT' ? '已公开全文' : '已公开题目与摘要')
  load()
}

async function revoke(p: any) {
  await ElMessageBox.confirm('撤回公开后，其他人的检索与下载将立即失效。确认撤回？', '撤回公开', { type: 'warning' })
  await archiveApi.revokeConsent(p.id)
  ElMessage.success('已撤回公开')
  load()
}

async function openSubmit() {
  picked.value = []
  form.value = { title: '', abstract: '', keywords: '' }
  submitVisible.value = true
  if (!policy.value) await loadPolicy()
  if (!eligibility.value) await loadEligibility()
}

/** 现场上传: 先 precheck 再调 /files/upload, 成功即把返回的 id 加入可选列表并自动勾选 */
async function handleUpload(file: File): Promise<boolean> {
  const blocked = precheck(file)
  if (blocked) {
    ElMessage.error(blocked)
    return false
  }
  uploading.value = true
  try {
    // business_type 只用于孤儿文件清理口径; 配额用尽(40013)与单文件超限(40010)由请求层分别提示
    const res = await uploadApi.file(file, { business_type: 'ARCHIVE_PACKAGE' })
    const item: ArchiveEligibleFile = {
      file_id: res.data.id,
      original_name: res.data.original_name,
      size: res.data.size,
      extension: res.data.extension ?? null,
      uploaded_at: null,
    }
    uploadedFiles.value.push(item)
    toggleFile(item, true)
    ElMessage.success('上传成功，已加入归档文件清单')
  } catch {
    /* 已提示 */
  } finally {
    uploading.value = false
  }
  return false
}

async function submitArchive() {
  if (!picked.value.length) {
    ElMessage.warning('请至少选择 1 个归档文件后再提交')
    return
  }
  submitting.value = true
  try {
    await archiveApi.submitStudent({
      title: form.value.title.trim() || undefined,
      abstract: form.value.abstract.trim() || undefined,
      keywords: form.value.keywords.trim() || undefined,
      files: picked.value.map((x) => ({ file_id: x.file_id, file_role: x.file_role })),
    })
    submitVisible.value = false
    ElMessage.success('已提交归档，等待学院确认')
    await load()
    await loadEligibility()
  } catch {
    /* 已提示; 弹窗保持打开以便修正后重试 */
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  load()
  loadEligibility()
})
</script>

<template>
  <header class="student-heading"><h1>毕业成果</h1><p>归档清单、最终版本与公开范围管理</p></header>

  <div class="archive-actions">
    <el-button type="primary" :disabled="!canSubmitArchive" @click="openSubmit">提交归档</el-button>
    <span class="hint">把最终版本的论文与源文件提交归档，提交后需学院确认</span>
  </div>

  <el-alert
    v-if="eligibility && !eligibility.eligible"
    class="eligible-alert"
    type="info"
    :closable="false"
    show-icon
    title="暂不能提交归档"
    :description="eligibility.reason || '答辩通过且毕业材料审核通过后，可在此提交成果归档'"
  />
  <el-alert
    v-else-if="eligibility && eligibility.package_status === 'PENDING'"
    class="eligible-alert"
    type="warning"
    :closable="false"
    show-icon
    title="归档待学院确认"
    description="学院确认前不能设置公开范围；确认后本页会显示已归档状态。"
  />

  <section v-if="loaded && !packages.length" class="page-card">
    <el-empty description="暂无归档成果">
      <p>答辩通过且毕业材料审核通过后，可在此提交成果归档。</p>
    </el-empty>
  </section>

  <div v-for="p in packages" :key="p.id" class="page-card pkg-card">
    <div class="pkg-head">
      <strong class="pkg-title">{{ p.title || p.topic_title || '毕业设计成果' }}</strong>
      <el-tag size="small" :type="p.status === 'ARCHIVED' ? 'success' : 'info'">{{ p.status_cn }}</el-tag>
      <el-tag v-if="p.status === 'PENDING'" size="small" type="warning">待学院确认</el-tag>
      <el-tag size="small" :type="p.visibility === 'PRIVATE' ? 'info' : 'warning'">{{ p.visibility_cn }}</el-tag>
    </div>
    <div class="pkg-meta">
      <span>{{ p.academic_year }}</span>
      <span v-if="p.result_score != null">答辩成绩 {{ p.result_score }} 分（{{ p.result_decision === 'PASS' ? '通过' : p.result_decision }}）</span>
      <span>归档于 {{ p.archived_at?.replace('T', ' ').slice(0, 16) || '—' }}</span>
      <span>{{ p.file_count }} 个文件</span>
      <el-link type="primary" @click="openDetail(p)">查看文件清单</el-link>
    </div>
    <p v-if="p.abstract" class="pkg-abstract">{{ p.abstract }}</p>
    <p v-if="p.keywords" class="pkg-keywords">关键词：{{ p.keywords }}</p>

    <div v-if="p.visibility === 'PRIVATE'" class="consent-row">
      <span class="hint">{{ p.status === 'PENDING' ? '学院确认归档后才能设置公开范围：' : '默认仅本人、导师与管理员可见。选择公开范围后其他人才可见：' }}</span>
      <el-button size="small" plain type="primary" :disabled="p.status === 'PENDING'" @click="grant(p, 'METADATA')">公开题目与摘要</el-button>
      <el-button size="small" plain type="warning" :disabled="p.status === 'PENDING'" @click="grant(p, 'FULL_TEXT')">公开全文</el-button>
    </div>
    <div v-else class="consent-row">
      <span class="hint">当前公开范围：{{ p.visibility_cn }}</span>
      <el-button size="small" plain type="danger" :disabled="p.status === 'PENDING'" @click="revoke(p)">撤回公开</el-button>
    </div>
  </div>

  <el-dialog v-model="submitVisible" title="提交成果归档" width="760px" destroy-on-close>
    <el-form label-width="72px" label-position="right">
      <el-form-item label="题名"><el-input v-model="form.title" maxlength="255" show-word-limit placeholder="默认取选题题目，可留空" /></el-form-item>
      <el-form-item label="摘要"><el-input v-model="form.abstract" type="textarea" :rows="3" placeholder="可选" /></el-form-item>
      <el-form-item label="关键词"><el-input v-model="form.keywords" maxlength="500" placeholder="可选，多个用逗号分隔" /></el-form-item>
    </el-form>

    <div class="picker-head">
      <strong>归档文件</strong>
      <span class="hint">{{ policyHint || '单个文件用途需逐一选择' }}</span>
    </div>
    <div class="picker-bar">
      <el-upload :show-file-list="false" :accept="acceptOf()" :disabled="uploading" :before-upload="handleUpload">
        <el-button :loading="uploading">{{ uploading ? '正在上传…' : '上传新文件' }}</el-button>
      </el-upload>
      <span class="hint">也可直接勾选下方已上传的文件</span>
    </div>

    <el-table :data="availableFiles" size="small" empty-text="该流程还没有可归档的已上传文件，请先上传">
      <el-table-column width="52">
        <template #default="{ row }">
          <el-checkbox :model-value="isPicked(row.file_id)" @change="toggleFile(row)" />
        </template>
      </el-table-column>
      <el-table-column prop="original_name" label="文件" min-width="220" show-overflow-tooltip />
      <el-table-column label="大小" width="96">
        <template #default="{ row }">{{ sizeText(row.size) }}</template>
      </el-table-column>
      <el-table-column label="用途" width="150">
        <template #default="{ row }">
          <el-select
            :model-value="roleOf(row.file_id)"
            size="small"
            :disabled="!isPicked(row.file_id)"
            @change="(v: any) => setRole(row.file_id, v)"
          >
            <el-option v-for="r in FILE_ROLES" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </template>
      </el-table-column>
    </el-table>

    <p class="pick-summary">
      已选 {{ picked.length }} 个文件
      <span v-if="!picked.length" class="hint">（至少选 1 个才能提交）</span>
    </p>

    <template #footer>
      <el-button @click="submitVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitArchive">提交归档</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="detailVisible" title="归档详情" width="640px" destroy-on-close>
    <template v-if="detail">
      <p class="pkg-abstract">{{ detail.abstract }}</p>
      <el-table :data="detail.files" size="small">
        <el-table-column prop="file_role" label="类型" width="110" />
        <el-table-column prop="original_name" label="文件" min-width="200" />
        <el-table-column prop="sha256" label="SHA256" width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-link type="primary" :href="row.url" target="_blank">下载</el-link>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-dialog>
</template>

<style scoped>
.archive-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.archive-actions .hint { font-size: 12px; color: #909399; }
.eligible-alert { margin-bottom: 14px; }
.pkg-card { margin-bottom: 16px; }
.pkg-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pkg-title { font-size: 15px; }
.pkg-meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #909399; margin: 10px 0; }
.pkg-abstract { font-size: 13px; color: #606266; }
.pkg-keywords { font-size: 12px; color: #909399; }
.consent-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px dashed #e4e7ed; }
.consent-row .hint { font-size: 12px; color: #909399; }
.picker-head { display: flex; align-items: baseline; gap: 10px; margin: 6px 0 10px; }
.picker-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.picker-bar .hint, .picker-head .hint { font-size: 12px; color: #909399; }
.pick-summary { margin-top: 10px; font-size: 13px; color: #606266; }
.pick-summary .hint { color: #e6a23c; }
@media (max-width: 640px) { .consent-row { flex-direction: column; align-items: flex-start; } }
</style>
