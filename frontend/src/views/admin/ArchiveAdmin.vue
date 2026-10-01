<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { archiveApi } from '@/api/defense'

const packages = ref<any[]>([])
const query = reactive({ academic_year: '', keyword: '', status: '' })
const createVisible = ref(false)
const createForm = reactive({ cycle_id: '' as any, title: '', abstract: '', keywords: '', files: '1:THESIS' })
const detail = ref<any | null>(null)
const detailVisible = ref(false)

const STATUS_TYPE: Record<string, string> = { ARCHIVED: 'success', PENDING: 'warning', REVOKED: 'danger' }

async function load() {
  const res = await archiveApi.search({ ...query, page: 1, page_size: 100 })
  packages.value = res.data.items || []
}

async function openDetail(p: any) {
  const res = await archiveApi.detail(p.id)
  detail.value = res.data
  detailVisible.value = true
}

async function createArchive() {
  const files = createForm.files.split(/[,，]/).map((s) => {
    const [file_id, file_role] = s.trim().split(':')
    return { file_id: Number(file_id), file_role: file_role || 'OTHER' }
  }).filter((f) => f.file_id)
  await archiveApi.create({
    cycle_id: Number(createForm.cycle_id), title: createForm.title, abstract: createForm.abstract,
    keywords: createForm.keywords, files,
  })
  ElMessage.success('归档完成')
  createVisible.value = false
  load()
}

async function revokePackage(p: any) {
  await ElMessageBox.confirm('撤回后检索与下载立即失效, 归档记录保留。确认撤回?', '撤回归档', { type: 'warning' })
  await archiveApi.revokePackage(p.id)
  ElMessage.success('已撤回')
  load()
}

async function exportList() {
  const res: any = await archiveApi.exportList()
  const url = URL.createObjectURL(new Blob([res as any], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'archive_list.csv'
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs"><div class="tab active">成果归档</div></div>
        <div class="tab-actions">
          <el-button @click="exportList">导出清单</el-button>
          <el-button type="primary" @click="createVisible = true">新建归档</el-button>
        </div>
      </div>

      <div class="filter-row">
        <el-select v-model="query.academic_year" placeholder="全部学年" clearable style="width: 140px">
          <el-option label="2025-2026" value="2025-2026" /><el-option label="2024-2025" value="2024-2025" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px">
          <el-option label="已归档" value="ARCHIVED" /><el-option label="已撤回" value="REVOKED" />
        </el-select>
        <el-input v-model="query.keyword" placeholder="学号/姓名/课题" clearable style="width: 200px" @keyup.enter="load" />
        <el-button type="primary" @click="load">查询</el-button>
      </div>

      <el-empty v-if="!packages.length" description="暂无归档成果" />
      <el-table v-else :data="packages" class="mat-table">
        <el-table-column prop="student_no" label="学号" width="90" />
        <el-table-column prop="student_name" label="学生" width="80" />
        <el-table-column prop="topic_title" label="课题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="academic_year" label="学年" width="95" />
        <el-table-column prop="major" label="专业" width="110" show-overflow-tooltip />
        <el-table-column prop="teacher_name" label="导师" width="80" />
        <el-table-column label="答辩成绩" width="90">
          <template #default="{ row }">{{ row.result_score != null ? `${row.result_score} 分` : '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag size="small" :type="(STATUS_TYPE[row.status] as any)">{{ row.status_cn }}</el-tag></template>
        </el-table-column>
        <el-table-column label="公开范围" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.visibility === 'PRIVATE' ? 'info' : 'warning'">{{ row.visibility_cn }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openDetail(row)">文件</el-button>
            <el-button v-if="row.status === 'ARCHIVED'" size="small" text type="danger" @click="revokePackage(row)">撤回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="detailVisible" title="归档文件" width="680px">
      <template v-if="detail">
        <p class="d-title">{{ detail.title || detail.topic_title }}</p>
        <p class="d-abstract">{{ detail.abstract }}</p>
        <el-table :data="detail.files" size="small">
          <el-table-column prop="file_role" label="类型" width="110" />
          <el-table-column prop="original_name" label="文件" min-width="180" />
          <el-table-column prop="sha256" label="SHA256" width="110" show-overflow-tooltip />
          <el-table-column label="下载" width="70">
            <template #default="{ row }"><el-link type="primary" :href="row.url" target="_blank">下载</el-link></template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>

    <el-dialog v-model="createVisible" title="新建归档包" width="560px">
      <el-alert type="info" :closable="false" style="margin-bottom: 14px"
                title="前置校验: 正式结论已发布且通过 + 毕业材料审核通过, 不满足将被拒绝" />
      <el-form label-width="90px">
        <el-form-item label="流程ID"><el-input v-model="createForm.cycle_id" placeholder="graduation_cycle.id" style="width: 200px" /></el-form-item>
        <el-form-item label="成果题名"><el-input v-model="createForm.title" /></el-form-item>
        <el-form-item label="摘要"><el-input v-model="createForm.abstract" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="关键词"><el-input v-model="createForm.keywords" /></el-form-item>
        <el-form-item label="文件清单">
          <el-input v-model="createForm.files" placeholder="file_id:角色, 如 1:THESIS,2:SOURCE_CODE" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="createArchive">归档</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-row { display: flex; gap: 12px; margin-bottom: 14px; }
.d-title { font-weight: 600; font-size: 15px; }
.d-abstract { font-size: 13px; color: #606266; margin-bottom: 12px; }
</style>
