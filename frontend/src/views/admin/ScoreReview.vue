<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { defenseApi, reviewApi, rubricApi } from '@/api/defense'

const sessions = ref<any[]>([])
const activeSession = ref<number | null>(null)
const rows = ref<any[]>([])
const preview = ref<any | null>(null)
const rubrics = ref<any[]>([])
const rubricDialog = ref(false)
const rubricListVisible = ref(false)
const rubricForm = ref({
  academic_year: '2025-2026', stage: 'FINAL', min_examiners: 3, pass_line: 60,
  weights: [
    { name: '报告质量', weight: 30 }, { name: '系统实现', weight: 40 }, { name: '答辩表现', weight: 30 },
  ],
})

const RESULT_TYPE: Record<string, string> = {
  PENDING: 'info', READY: 'warning', UNDER_REVIEW: 'danger', PUBLISHED: 'success', WITHDRAWN: 'info',
}

async function loadSessions() {
  const res = await defenseApi.adminList({ academic_year: '2025-2026', page: 1, page_size: 50 })
  sessions.value = res.data.items || []
  if (!activeSession.value && sessions.value.length) {
    activeSession.value = sessions.value[0].id
    loadResults()
  }
}

async function loadResults() {
  const res = await reviewApi.results(activeSession.value ?? undefined)
  rows.value = res.data || []
}

async function pickSession(s: any) {
  activeSession.value = s.id
  await loadResults()
}

async function doPreview(row: any) {
  if (!row.result) {
    const res = await reviewApi.generateResult(row.id)
    row.result = { id: res.data.id, status: res.data.status }
  }
  const res = await reviewApi.preview(row.result.id)
  preview.value = res.data
}

async function calculate(row: any) {
  const resultId = row.result.id
  const res = await reviewApi.calculate(resultId)
  const score = res.data?.score
  ElMessage.success(`已复算: ${score ?? '—'} 分 ${res.data?.decision === 'PASS' ? '通过' : res.data?.decision === 'FAIL' ? '不通过' : ''}`.trim())
  await loadResults()
  // 复算会改写分数与结论明细: 立刻按同一 result 重拉预览(复用 doPreview), 不再要求用户再点一次"复算预览"
  const fresh = rows.value.find((r: any) => r.result?.id === resultId) || row
  await doPreview(fresh)
}

async function publish(row: any) {
  await reviewApi.publish(row.result.id)
  ElMessage.success('正式结论已发布并通知学生')
  loadResults()
}

async function withdraw(row: any) {
  const { value } = await ElMessageBox.prompt('撤回原因(学生端将显示复核中, 至少 2 个字)', '撤回发布', { inputPattern: /[\s\S]{2,}/, inputErrorMessage: '至少 2 个字' })
  await reviewApi.withdraw(row.result.id, value)
  ElMessage.success('已撤回')
  loadResults()
}

async function reopen(row: any) {
  const { value } = await ElMessageBox.prompt('重开原因', '复核重开评分', { inputPattern: /.+/, inputErrorMessage: '必填' })
  // 通过复算预览取各评委最新有效评分记录 id
  const res = await reviewApi.preview(row.result.id)
  const detail = res.data.detail || []
  if (!detail.length) { ElMessage.warning('暂无评分记录'); return }
  for (const d of detail) {
    await reviewApi.reopen(d.score_id, value)
  }
  ElMessage.success('已重开全部评分, 等待评委修改后重新提交')
  loadResults()
}

async function loadRubrics() {
  const res = await rubricApi.list({})
  rubrics.value = res.data || []
}

async function saveRubric() {
  await rubricApi.create({ ...rubricForm.value })
  ElMessage.success('评分表已创建(未启用)')
  rubricDialog.value = false
  loadRubrics()
}

async function enableRubric(r: any) {
  await rubricApi.enable(r.id)
  ElMessage.success(`已启用 v${r.version} 并冻结`)
  loadRubrics()
}

onMounted(() => { loadSessions(); loadRubrics() })
</script>

<template>
  <div class="page-wrap">
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs"><div class="tab active">成绩复核</div></div>
        <div class="tab-actions">
          <el-button @click="rubricListVisible = true">评分表版本</el-button>
          <el-button type="primary" @click="rubricDialog = true">新建评分表</el-button>
        </div>
      </div>

      <div class="layout-split">
        <div class="session-list">
          <div class="list-title">场次</div>
          <div v-for="s in sessions" :key="s.id" class="session-row" :class="{ active: activeSession === s.id }" @click="pickSession(s)">
            <b>{{ s.title || `场次#${s.id}` }}</b>
            <span class="muted">{{ s.stage_cn }} · {{ s.starts_at?.slice(5, 16).replace('T', ' ') }} · {{ s.status_cn }}</span>
          </div>
          <el-empty v-if="!sessions.length" description="暂无场次" :image-size="60" />
        </div>

        <div class="result-main">
          <el-empty v-if="!rows.length" description="该场次暂无汇总记录" :image-size="70" />
          <el-table v-else :data="rows" class="mat-table">
            <el-table-column prop="student_no" label="学号" width="90" />
            <el-table-column prop="student_name" label="学生" width="80" />
            <el-table-column prop="topic_title" label="课题" min-width="160" show-overflow-tooltip />
            <el-table-column prop="seat_status_cn" label="席位" width="80" />
            <el-table-column label="评分进度" width="90">
              <template #default="{ row }">{{ row.score_count }} 份</template>
            </el-table-column>
            <el-table-column label="结论状态" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.result" size="small" :type="(RESULT_TYPE[row.result.status] as any)">{{ row.result.status_cn }}</el-tag>
                <span v-else class="muted">未生成</span>
              </template>
            </el-table-column>
            <el-table-column prop="result.score" label="成绩" width="70" />
            <el-table-column label="操作" width="300" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="doPreview(row)">复算预览</el-button>
                <el-button v-if="row.result?.status === 'PENDING'" size="small" text type="warning" @click="calculate(row)">复算结论</el-button>
                <el-button v-if="row.result && ['READY', 'UNDER_REVIEW'].includes(row.result.status)" size="small" type="success" @click="publish(row)">发布</el-button>
                <el-button v-if="row.result?.status === 'PUBLISHED'" size="small" type="warning" @click="withdraw(row)">撤回</el-button>
                <el-button v-if="row.result && ['PUBLISHED', 'UNDER_REVIEW'].includes(row.result.status)" size="small" text type="danger" @click="reopen(row)">复核重开</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- 复算预览 -->
          <div v-if="preview" class="preview-card">
            <div class="preview-head">
              <b>汇总复算（{{ preview.valid_count }}/{{ preview.min_required }} 份有效评分）</b>
              <el-tag v-if="preview.recompute_match === true" type="success" size="small">与发布成绩一致</el-tag>
              <el-tag v-if="preview.publishable" type="warning" size="small">可发布</el-tag>
              <el-button text size="small" @click="preview = null">收起</el-button>
            </div>
            <div class="preview-body">
              <div v-for="(v, k) in preview.item_avg" :key="k" class="avg-row">{{ k }} 均分：<b>{{ v ?? '缺项' }}</b></div>
              <div class="avg-total">加权总分：<b>{{ preview.score ?? '—' }}</b>
                <el-tag v-if="preview.decision" size="small" :type="preview.decision === 'PASS' ? 'success' : 'danger'">
                  {{ preview.decision === 'PASS' ? '通过' : '不通过' }}
                </el-tag>
              </div>
              <div class="detail-note" v-if="!preview.publishable">
                未达发布条件：评委人数/评分完整性/到场状态需全部满足。
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 评分表配置 -->
    <el-dialog v-model="rubricDialog" title="评分表配置(学年+学院+阶段)" width="620px">
      <el-form label-width="90px">
        <el-form-item label="学年"><el-input v-model="rubricForm.academic_year" style="width: 200px" /></el-form-item>
        <el-form-item label="阶段">
          <el-select v-model="rubricForm.stage" style="width: 200px">
            <el-option label="开题答辩" value="OPENING" /><el-option label="中期答辩" value="MIDTERM" /><el-option label="终期答辩" value="FINAL" />
          </el-select>
        </el-form-item>
        <el-form-item label="最低评委数"><el-input-number v-model="rubricForm.min_examiners" :min="1" :max="20" /></el-form-item>
        <el-form-item label="通过线"><el-input-number v-model="rubricForm.pass_line" :min="0" :max="100" /></el-form-item>
        <el-form-item label="评分项">
          <div class="weights-editor">
            <div v-for="(w, i) in rubricForm.weights" :key="i" class="weight-row">
              <el-input v-model="w.name" style="width: 160px" placeholder="评分项" />
              <el-input-number v-model="w.weight" :min="1" :max="100" />
              <el-button text type="danger" @click="rubricForm.weights.splice(i, 1)">删除</el-button>
            </div>
            <div class="weight-total">
              合计: {{ rubricForm.weights.reduce((s, w) => s + w.weight, 0) }}%
              <el-button size="small" text type="primary" @click="rubricForm.weights.push({ name: '', weight: 20 })">+ 添加</el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rubricDialog = false">取消</el-button>
        <el-button type="primary" @click="saveRubric">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rubricListVisible" title="评分表版本(启用后冻结)" width="680px">
      <el-table :data="rubrics" size="small">
        <el-table-column prop="academic_year" label="学年" width="100" />
        <el-table-column prop="stage_cn" label="阶段" width="90" />
        <el-table-column prop="version" label="版本" width="60" />
        <el-table-column prop="min_examiners" label="最低评委" width="80" />
        <el-table-column label="权重" min-width="180">
          <template #default="{ row }">{{ row.weights.map((w: any) => `${w.name}${w.weight}%`).join(' / ') }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.enabled" size="small" type="success">已启用</el-tag><el-tag v-else size="small">未启用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="!row.enabled" size="small" text type="primary" @click="enableRubric(row)">启用</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.layout-split { display: grid; grid-template-columns: 260px 1fr; gap: 18px; }
.session-list { border-right: 1px solid #ebeef5; padding-right: 12px; }
.list-title { font-weight: 600; font-size: 13px; margin-bottom: 10px; }
.session-row { padding: 10px 12px; border-radius: 8px; cursor: pointer; margin-bottom: 6px; display: flex; flex-direction: column; gap: 2px; }
.session-row:hover, .session-row.active { background: #f0f7ff; }
.session-row .muted { font-size: 12px; color: #909399; }
.muted { color: #909399; font-size: 12px; }
.preview-card { margin-top: 14px; background: #f7f9fc; border-radius: 10px; padding: 14px 18px; }
.preview-head { display: flex; gap: 10px; align-items: center; }
.preview-body { margin-top: 8px; font-size: 13px; }
.avg-row { padding: 2px 0; }
.avg-total { margin-top: 6px; font-size: 14px; }
.avg-total b { color: #1878fd; font-size: 18px; margin: 0 6px; }
.detail-note { color: #e6a23c; font-size: 12px; margin-top: 8px; }
.weights-editor { width: 100%; }
.weight-row { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.weight-total { font-size: 12px; color: #909399; }
@media (max-width: 900px) { .layout-split { grid-template-columns: 1fr; } .session-list { border-right: none; } }
</style>
