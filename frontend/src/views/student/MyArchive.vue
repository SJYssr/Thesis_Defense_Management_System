<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { archiveApi } from '@/api/defense'

const packages = ref<any[]>([])
const loaded = ref(false)
const detailVisible = ref(false)
const detail = ref<any | null>(null)

async function load() {
  const res = await archiveApi.search({})
  packages.value = res.data.items || []
  loaded.value = true
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

onMounted(load)
</script>

<template>
  <header class="student-heading"><h1>毕业成果</h1><p>归档清单、最终版本与公开范围管理</p></header>

  <section v-if="loaded && !packages.length" class="page-card">
    <el-empty description="暂无归档成果">
      <p>正式答辩结论发布、毕业材料审核通过后，学院将成果统一归档；归档完成后可在此管理公开范围。</p>
    </el-empty>
  </section>

  <div v-for="p in packages" :key="p.id" class="page-card pkg-card">
    <div class="pkg-head">
      <strong class="pkg-title">{{ p.title || p.topic_title || '毕业设计成果' }}</strong>
      <el-tag size="small" :type="p.status === 'ARCHIVED' ? 'success' : 'info'">{{ p.status_cn }}</el-tag>
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
      <span class="hint">默认仅本人、导师与管理员可见。选择公开范围后其他人才可见：</span>
      <el-button size="small" plain type="primary" @click="grant(p, 'METADATA')">公开题目与摘要</el-button>
      <el-button size="small" plain type="warning" @click="grant(p, 'FULL_TEXT')">公开全文</el-button>
    </div>
    <div v-else class="consent-row">
      <span class="hint">当前公开范围：{{ p.visibility_cn }}</span>
      <el-button size="small" plain type="danger" @click="revoke(p)">撤回公开</el-button>
    </div>
  </div>

  <el-dialog v-model="detailVisible" title="归档详情" width="640px">
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
.pkg-card { margin-bottom: 16px; }
.pkg-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pkg-title { font-size: 15px; }
.pkg-meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #909399; margin: 10px 0; }
.pkg-abstract { font-size: 13px; color: #606266; }
.pkg-keywords { font-size: 12px; color: #909399; }
.consent-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px dashed #e4e7ed; }
.consent-row .hint { font-size: 12px; color: #909399; }
@media (max-width: 640px) { .consent-row { flex-direction: column; align-items: flex-start; } }
</style>
