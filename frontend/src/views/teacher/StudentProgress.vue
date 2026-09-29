<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { progressApi } from '@/api/business'
import { aiApi } from '@/api/common'
import StatusTag from '@/components/StatusTag.vue'
import ProcessTimeline from '@/components/ProcessTimeline.vue'

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, risk_level: '', keyword: '' })

const detailVisible = ref(false)
const detail = ref<any>(null)
const analysis = ref<any>(null)
const analysing = ref(false)
const reminderVisible = ref(false)
const reminderForm = reactive({ title: '', content: '' })
const reminding = ref(false)

async function load() {
  const res = await progressApi.teacherStudents({
    ...query, risk_level: query.risk_level || undefined, keyword: query.keyword || undefined,
  })
  items.value = res.data.items
  total.value = res.data.total
}

async function openDetail(row: any) {
  detail.value = (await progressApi.teacherStudentDetail(row.student_id)).data
  analysis.value = null
  detailVisible.value = true
}

async function analyse() {
  analysing.value = true
  try {
    const res = await aiApi.analyseProgress(detail.value.student_id)
    analysis.value = res.data
  } catch {
    /* 已提示 */
  } finally {
    analysing.value = false
  }
}

function openReminder(preset?: string) {
  reminderForm.title = '毕业设计进度提醒'
  reminderForm.content = preset || analysis.value?.recommended_reminder || `您当前处于「${detail.value.current_stage_cn}」阶段, 请按计划推进。`
  reminderVisible.value = true
}

async function sendReminder() {
  reminding.value = true
  try {
    await progressApi.sendReminder(detail.value.student_id, { ...reminderForm })
    ElMessage.success('提醒已发送')
    reminderVisible.value = false
  } catch {
    /* 已提示 */
  } finally {
    reminding.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">学生毕设进度</h3>
    </div>
    <div class="filter-bar">
      <el-input v-model="query.keyword" placeholder="姓名/学号" clearable style="width: 180px" @keyup.enter="load" />
      <el-select v-model="query.risk_level" placeholder="风险等级" clearable style="width: 130px" @change="load">
        <el-option label="正常" value="NORMAL" />
        <el-option label="警告" value="WARNING" />
        <el-option label="高风险" value="HIGH" />
      </el-select>
      <el-button type="primary" @click="load">查询</el-button>
    </div>
    <el-table :data="items" border>
      <el-table-column prop="student_name" label="学生" width="100" />
      <el-table-column prop="student_no" label="学号" width="110" />
      <el-table-column prop="topic_title" label="题目" min-width="200" show-overflow-tooltip />
      <el-table-column prop="current_stage_cn" label="当前阶段" width="90" />
      <el-table-column label="进度" width="170">
        <template #default="{ row }">
          <el-progress :percentage="row.progress_percent" :stroke-width="8"
                       :color="row.progress_percent >= 60 ? '#67c23a' : '#409eff'" />
        </template>
      </el-table-column>
      <el-table-column label="风险" width="85">
        <template #default="{ row }"><StatusTag :status="row.risk_level" /></template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">详情</el-button>
          <el-button size="small" type="warning" plain @click="detail = row; openReminder()">提醒</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-dialog v-model="detailVisible" :title="`学生详情 - ${detail?.student_name}`" width="820px" top="5vh">
      <div v-if="detail" style="max-height: 70vh; overflow-y: auto">
        <el-descriptions :column="3" border style="margin-bottom: 16px">
          <el-descriptions-item label="学号">{{ detail.student_no }}</el-descriptions-item>
          <el-descriptions-item label="题目">{{ detail.topic_title || '-' }}</el-descriptions-item>
          <el-descriptions-item label="当前阶段">{{ detail.current_stage_cn }}</el-descriptions-item>
        </el-descriptions>
        <ProcessTimeline v-if="detail.timeline" :timeline="detail.timeline" />

        <el-divider>
          <el-button size="small" type="primary" :loading="analysing" @click="analyse">
            {{ analysing ? 'AI 分析中...' : 'AI 进度分析' }}
          </el-button>
        </el-divider>

        <template v-if="analysis">
          <el-alert :type="analysis.risk_level === 'HIGH' ? 'error' : analysis.risk_level === 'WARNING' ? 'warning' : 'success'"
                    :closable="false" style="margin-bottom: 12px">
            <template #title>
              AI 风险评估: <StatusTag :status="analysis.risk_level" />
              (规则引擎: {{ analysis.rule_snapshot?.rule_risk }} · 进度 {{ analysis.rule_snapshot?.progress_percent }}%)
            </template>
          </el-alert>
          <div class="analysis-box markdown-body">
            <p><strong>总体分析:</strong> {{ analysis.summary }}</p>
            <p><strong>存在问题:</strong></p>
            <ul><li v-for="(p, i) in analysis.problems" :key="i">{{ p }}</li></ul>
            <p><strong>改进建议:</strong></p>
            <ul><li v-for="(s, i) in analysis.suggestions" :key="i">{{ s }}</li></ul>
          </div>
          <div style="display: flex; justify-content: flex-end; margin-top: 8px">
            <el-button type="warning" @click="openReminder(analysis.recommended_reminder)">
              使用 AI 建议发送提醒
            </el-button>
          </div>
        </template>
      </div>
    </el-dialog>

    <el-dialog v-model="reminderVisible" title="发送提醒" width="520px">
      <el-form label-width="60px">
        <el-form-item label="标题">
          <el-input v-model="reminderForm.title" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="reminderForm.content" type="textarea" :rows="4" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reminderVisible = false">取消</el-button>
        <el-button type="primary" :loading="reminding" @click="sendReminder">发送</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.analysis-box {
  background: #f8f9fb;
  border-radius: 8px;
  padding: 14px;
}
</style>
