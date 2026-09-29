<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { progressApi } from '@/api/business'
import ProcessTimeline from '@/components/ProcessTimeline.vue'
import StatusTag from '@/components/StatusTag.vue'

const data = ref<any>(null)
const timelineData = ref<any[]>([])

async function load() {
  const res = await progressApi.mine()
  data.value = res.data
  timelineData.value = res.data.timeline || []
}

onMounted(load)
</script>

<template>
  <div class="page-card" v-if="data">
    <div class="page-header">
      <h3 class="page-title">毕设进度</h3>
      <StatusTag :status="data.risk_level" />
    </div>

    <div style="display: flex; align-items: center; gap: 24px; margin-bottom: 20px">
      <el-progress type="dashboard" :percentage="data.progress_percent" :width="120"
                   :color="data.progress_percent >= 60 ? '#67c23a' : '#409eff'" />
      <div>
        <div style="font-size: 15px; margin-bottom: 8px">
          当前阶段: <el-tag>{{ data.current_stage_cn }}</el-tag>
        </div>
        <div style="color: #606266; font-size: 13px; line-height: 2">
          <div>题目: {{ data.selection?.topic_title || data.topic_title || '未选题' }}</div>
          <div v-if="data.teacher_name">指导教师: {{ data.teacher_name }}</div>
          <div v-if="data.risk_reason" style="color: #f56c6c">风险说明: {{ data.risk_reason }}</div>
          <template v-if="data.deadline_info?.days_left != null">
            <div>距本阶段截止: {{ data.deadline_info.days_left }} 天
              ({{ (data.deadline_info.deadline || '').slice(0, 10) }})</div>
          </template>
        </div>
      </div>
    </div>

    <ProcessTimeline :timeline="timelineData" />

    <el-divider />

    <h3 style="margin: 0 0 12px">各阶段审核轨迹</h3>
    <el-tabs>
      <el-tab-pane v-for="t in timelineData" :key="t.stage" :label="t.stage_cn">
        <div class="stage-row">
          <div>状态: <StatusTag :status="t.status" /></div>
          <div style="color: #909399; font-size: 13px; margin-top: 4px">
            提交时间: {{ (t.submitted_at || '-').replace('T', ' ').slice(0, 16) }} ·
            审核时间: {{ (t.reviewed_at || '-').replace('T', ' ').slice(0, 16) }}
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.stage-row {
  padding: 8px 0;
  font-size: 14px;
}
</style>
