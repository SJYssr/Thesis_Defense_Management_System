<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { reviewApi } from '@/api/defense'

const queue = ref<any[]>([])
const activeTab = ref('todo')
const detail = ref<any | null>(null)
const detailVisible = ref(false)
const scores = reactive<Record<string, number>>({})
const comment = ref('')
const submitting = ref(false)

const mySubmitted = computed(() => queue.value.filter((q) => ['SUBMITTED'].includes(q.my_score?.status)))
const todo = computed(() => queue.value.filter((q) => !q.my_score || !['SUBMITTED'].includes(q.my_score.status)))

async function load() {
  const res = await reviewApi.myQueue()
  queue.value = res.data || []
}

async function openReview(item: any) {
  const res = await reviewApi.getReview(item.id)
  detail.value = res.data
  Object.keys(scores).forEach((k) => delete scores[k])
  const my = res.data.my_score
  if (my?.scores) Object.assign(scores, my.scores)
  comment.value = my?.comment || ''
  detailVisible.value = true
}

function weightTotal() {
  const weights = detail.value?.rubric?.weights || []
  let total = 0
  for (const w of weights) {
    const v = scores[w.name]
    if (v != null) total += v * w.weight / 100
  }
  return Math.round(total * 10) / 10
}

function validateScores() {
  const weights = detail.value?.rubric?.weights || []
  for (const w of weights) {
    const v = scores[w.name]
    if (v == null) { ElMessage.warning(`请填写评分项「${w.name}」`); return false }
    if (v < 0 || v > 100) { ElMessage.warning(`「${w.name}」须在 0~100 之间`); return false }
  }
  return true
}

async function saveDraft() {
  await reviewApi.saveDraft(detail.value.assignment.id, { scores, comment: comment.value })
  ElMessage.success('草稿已暂存')
  load()
}

async function submit() {
  if (!validateScores()) return
  submitting.value = true
  try {
    await reviewApi.submitScore(detail.value.assignment.id, { scores, comment: comment.value })
    ElMessage.success('评分已提交并锁定')
    detailVisible.value = false
    load()
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs">
          <div class="tab" :class="{ active: activeTab === 'todo' }" @click="activeTab = 'todo'">我的待评（{{ todo.length }}）</div>
          <div class="tab" :class="{ active: activeTab === 'done' }" @click="activeTab = 'done'">已提交（{{ mySubmitted.length }}）</div>
        </div>
      </div>

      <el-empty v-if="!queue.length" description="暂无被指派的评审任务" />
      <el-table v-else :data="activeTab === 'todo' ? todo : mySubmitted" class="mat-table">
        <el-table-column prop="student_name" label="学生" width="90" />
        <el-table-column prop="student_no" label="学号" width="100" />
        <el-table-column prop="topic_title" label="课题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="session.stage_cn" label="阶段" width="100" />
        <el-table-column label="答辩时间" width="160">
          <template #default="{ row }">{{ row.session.starts_at?.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="我的评分" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.my_score" size="small" :type="row.my_score.status === 'SUBMITTED' ? 'success' : row.my_score.status === 'REOPENED' ? 'danger' : 'info'">
              {{ row.my_score.status_cn }} v{{ row.my_score.version }}
            </el-tag>
            <span v-else class="muted">未评阅</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openReview(row)">{{ row.my_score?.status === 'SUBMITTED' ? '查看' : '评阅' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="detailVisible" size="720px" :title="`评阅工作台 - ${detail?.assignment?.student_name || ''}`">
      <template v-if="detail">
        <div class="meta-line">
          <el-tag size="small">{{ detail.session.stage_cn }}</el-tag>
          <span>{{ detail.session.starts_at?.replace('T', ' ').slice(0, 16) }}</span>
          <span v-if="detail.session.location">· {{ detail.session.location }}</span>
          <el-tag v-if="detail.my_score" size="small" type="success">{{ detail.my_score.status_cn }}</el-tag>
        </div>

        <div class="block-title">评审材料（只读，最小必要范围）</div>
        <div class="material-block">
          <p><b>课题：</b>{{ detail.materials.topic_title || '—' }}</p>
          <template v-for="(m, key) in detail.materials" :key="key">
            <div v-if="m && typeof m === 'object' && 'status' in m" class="stage-material">
              <b>{{ key === 'opening' ? '开题报告' : key === 'midterm' ? '中期报告' : '终期材料' }}（{{ m.status }}）</b>
              <div v-for="(v, k) in m.content" :key="k" class="field-row">
                <span class="k">{{ k }}：</span>{{ v || '—' }}
              </div>
            </div>
          </template>
          <div v-if="detail.materials.graduation_files?.length">
            <b>毕业材料文件</b>
            <div v-for="f in detail.materials.graduation_files" :key="f.file_id" class="field-row">
              <el-link type="primary" :href="f.url" target="_blank">{{ f.original_name }}</el-link>
            </div>
          </div>
        </div>

        <div class="block-title">评分表（{{ detail.rubric ? `v${detail.rubric.version} · 最低评委 ${detail.rubric.min_examiners} 人` : '未配置' }}）</div>
        <template v-if="detail.rubric">
          <div v-for="w in detail.rubric.weights" :key="w.name" class="score-row">
            <span class="name">{{ w.name }}<i class="weight">{{ w.weight }}%</i></span>
            <el-input-number v-model="scores[w.name]" :min="0" :max="100" :disabled="detail.my_score?.status === 'SUBMITTED'" size="small" />
          </div>
          <div class="score-total">加权预览：<b>{{ weightTotal() }}</b> / 100（通过线 {{ detail.rubric.pass_line }}）</div>
          <el-input v-model="comment" type="textarea" :rows="3" placeholder="结构化评语：说明评分理由与改进建议"
                    :disabled="detail.my_score?.status === 'SUBMITTED'" style="margin-top: 10px" />
          <div class="foot-actions" v-if="detail.my_score?.status !== 'SUBMITTED'">
            <el-button @click="saveDraft">暂存草稿</el-button>
            <el-button type="primary" :loading="submitting" @click="submit">正式提交(锁定)</el-button>
          </div>
          <div v-else class="locked-tip">评分已提交锁定。如需修改，请申请管理员复核重开。</div>
        </template>
        <el-alert v-else type="warning" title="本学年未配置启用的评分表，无法评分" />
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.muted { color: #909399; font-size: 12px; }
.meta-line { display: flex; gap: 10px; align-items: center; font-size: 13px; margin-bottom: 14px; }
.block-title { font-size: 14px; font-weight: 600; margin: 18px 0 10px; }
.material-block { background: #f7f9fc; border-radius: 8px; padding: 12px 16px; font-size: 13px; max-height: 40vh; overflow: auto; }
.stage-material { border-top: 1px dashed #e4e7ed; margin-top: 8px; padding-top: 8px; }
.field-row { padding: 2px 0; color: #606266; }
.field-row .k { color: #909399; }
.score-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; font-size: 13px; }
.score-row .weight { color: #909399; font-style: normal; margin-left: 8px; font-size: 12px; }
.score-total { padding: 10px 0; font-size: 13px; text-align: right; }
.score-total b { color: #1878fd; font-size: 16px; }
.foot-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px; }
.locked-tip { text-align: right; color: #909399; font-size: 12px; padding-top: 10px; }
</style>
