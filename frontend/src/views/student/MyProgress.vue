<template>
  <div class="my-progress">
    <header class="page-heading"><h1>我的毕设进度</h1><p>查看当前阶段、流程节点、审核记录和风险提醒</p></header>

    <!-- 总体进度 -->
    <section class="page-card overall-card">
      <div class="oc-head"><h3>总体进度</h3><strong>{{ data?.progress_percent || 0 }}%</strong></div>
      <el-progress :percentage="data?.progress_percent || 0" :show-text="false" :stroke-width="14" color="#1878fd" />
      <div class="stage-strip">
        <template v-for="(t, i) in timelineData" :key="t.stage">
          <div class="stage-cell">
            <span class="sc-name">{{ t.stage_cn }}</span>
            <span class="sc-status" :class="stageCls(t)">{{ stageCn(t) }}</span>
          </div>
          <i v-if="i < timelineData.length - 1" class="sc-line" />
        </template>
      </div>
    </section>

    <!-- 正式答辩 -->
    <section class="page-card defense-strip">
      <h3 class="block-title">正式答辩结论</h3>
      <p v-if="data?.formal_defense?.status === 'PUBLISHED'" class="defense-published">
        {{ data.formal_defense.decision === 'PASS' ? '通过' : data.formal_defense.decision === 'FAIL' ? '未通过' : '待修改或延期' }}
        <span v-if="data.formal_defense.score != null" class="defense-score">{{ data.formal_defense.score }} 分</span>
      </p>
      <p v-else-if="data?.formal_defense?.status === 'NOT_SCHEDULED'" class="defense-muted">尚未安排正式答辩。材料审核结果与 AI 模拟答辩成绩均不代表正式答辩结论。</p>
      <p v-else class="defense-muted">正式答辩结果尚未发布。</p>
    </section>

    <div class="progress-split">
      <!-- 流程时间线 -->
      <section class="page-card">
        <h3 class="block-title">流程时间线</h3>
        <div v-if="timelineData.length" class="flow-timeline">
          <div v-for="(t, i) in timelineData" :key="t.stage" class="ft-row">
            <div class="ft-left">
              <i class="ft-dot" :class="t.done ? 'done' : t.status === 'NOT_SUBMITTED' ? 'todo' : 'doing'" />
              <i v-if="i < timelineData.length - 1" class="ft-line" />
            </div>
            <span class="ft-date">{{ (t.reviewed_at || t.submitted_at || '—').slice(5, 10).replace('-', '/') }}</span>
            <span class="ft-text">{{ flowText(t) }}</span>
            <span class="status-tag" :class="tagCls(t)">{{ stageCn(t) }}</span>
          </div>
        </div>
        <el-empty v-else description="选题后显示进度时间线" :image-size="70" />
      </section>

      <!-- 风险与提醒 -->
      <section class="page-card risk-card">
        <h3 class="block-title">风险与提醒</h3>
        <span class="status-tag risk-pill" :class="data?.risk_level === 'NORMAL' ? 's-passed' : data?.risk_level === 'WARNING' ? 's-warn' : 's-high'">
          {{ data?.risk_level === 'NORMAL' ? '正常' : data?.risk_level === 'WARNING' ? '滞后' : '预警' }}
        </span>
        <div class="risk-callout">
          <strong>{{ data?.risk_reason || '按计划推进毕业设计' }}</strong>
          <p v-if="data?.deadline_info?.days_left != null">距离本阶段截止仅剩 {{ data.deadline_info.days_left }} 天</p>
          <el-button type="primary" size="small" @click="$router.push('/student/ai-defense')">查看 AI 分析</el-button>
        </div>

        <h4>教师提醒</h4>
        <div v-if="data?.reminders?.length" class="reminder-list">
          <div v-for="r in data.reminders" :key="r.id" class="reminder-row">
            <span class="r-date">{{ (r.created_at || '').slice(5, 10).replace('-', '/') }}</span>
            <span class="r-text" :title="r.content">{{ r.title }}</span>
          </div>
        </div>
        <p v-else class="reminder-empty">暂无教师提醒</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { progressApi } from '@/api/business'

const data = ref<any>(null)
const timelineData = ref<any[]>([])

async function load() {
  const res = await progressApi.mine()
  data.value = res.data
  timelineData.value = res.data.timeline || []
}

function stageCn(t: any) {
  if (t.status === 'APPROVED') return '已通过'
  if (t.status === 'SUBMITTED' || t.status === 'PENDING') return '待审核'
  if (t.status === 'DRAFT') return '草稿'
  if (t.status === 'REJECTED') return '已驳回'
  if (t.status === 'RESUBMIT_REQUIRED') return '需重新提交'
  return '未开始'
}
function stageCls(t: any) {
  if (t.status === 'APPROVED') return 'ok'
  if (t.status === 'NOT_SUBMITTED') return 'todo'
  return 'doing'
}
function tagCls(t: any) {
  if (t.status === 'APPROVED') return 's-passed'
  if (t.status === 'NOT_SUBMITTED') return 's-withdraw'
  if (t.status === 'REJECTED') return 's-failed'
  if (t.status === 'RESUBMIT_REQUIRED') return 's-warn'
  return 's-info'
}
function flowText(t: any) {
  const stage = t.stage_cn || t.stage
  if (t.status === 'APPROVED') return `${stage}审核通过`
  if (t.status === 'SUBMITTED' || t.status === 'PENDING') return `已提交${stage}，等待审核`
  if (t.status === 'DRAFT') return `${stage}草稿待完善`
  if (t.status === 'REJECTED') return `${stage}被驳回`
  if (t.status === 'RESUBMIT_REQUIRED') return `${stage}需重新提交`
  return `准备${stage}材料`
}

onMounted(load)
</script>

<style scoped>
.page-heading { margin-bottom: 18px; }
.page-heading h1 { font-size: 26px; color: #1f2d3d; margin: 0 0 6px; }
.page-heading p { color: #93a0b4; font-size: 13px; margin: 0; }

/* 总体进度 */
.overall-card { padding: 24px 28px; margin-bottom: 16px; }
.oc-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.oc-head h3 { font-size: 16px; color: #1f2d3d; margin: 0; }
.oc-head strong { font-size: 34px; color: #1878fd; font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif; }
.stage-strip {
  display: flex;
  align-items: center;
  padding-top: 24px;
}
.stage-cell { display: flex; align-items: center; gap: 10px; }
.sc-name { font-size: 13px; color: #1f2d3d; font-weight: 500; }
.sc-status { font-size: 12px; }
.sc-status.ok { color: #16a34a; }
.sc-status.doing { color: #1878fd; }
.sc-status.todo { color: #a8b3c5; }
.sc-line { flex: 1; height: 1px; background: #e5eaf2; margin: 0 18px; min-width: 24px; }

/* 正式答辩 */
.defense-strip { padding: 18px 28px; margin-bottom: 16px; display: flex; align-items: center; gap: 18px; }
.defense-strip .block-title { margin: 0; }
.defense-strip p { margin: 0; font-size: 13.5px; }
.defense-published { color: #16a34a; font-weight: 600; }
.defense-score { margin-left: 10px; color: #1878fd; }
.defense-muted { color: #93a0b4; }

/* 双栏 */
.progress-split {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.block-title { font-size: 15px; color: #1f2d3d; }

/* 时间线 */
.flow-timeline { padding-top: 6px; }
.ft-row { display: flex; align-items: center; gap: 14px; min-height: 56px; }
.ft-left { position: relative; width: 12px; align-self: stretch; display: flex; justify-content: center; }
.ft-dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  margin-top: 22px;
  z-index: 1;
}
.ft-dot.done { background: #1878fd; }
.ft-dot.doing { background: #1878fd; box-shadow: 0 0 0 3px rgba(24, 120, 253, 0.18); }
.ft-dot.todo { background: #d5dde8; }
.ft-line { position: absolute; left: 50%; top: 33px; bottom: -8px; width: 1.5px; background: #e5eaf2; transform: translateX(-50%); }
.ft-row:last-child .ft-line { display: none; }
.ft-date { width: 44px; color: #93a0b4; font-size: 12.5px; flex-shrink: 0; }
.ft-text { flex: 1; min-width: 0; color: #1f2d3d; font-size: 13.5px; overflow-wrap: anywhere; }

/* 状态标签 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
  flex-shrink: 0;
}
.s-passed { color: #27ae60; background: #e4f7ec; }
.s-info { color: #1878fd; background: #e3effe; }
.s-warn { color: #d97706; background: #fef3c7; }
.s-high { color: #e74c3c; background: #fdeaea; }
.s-withdraw { color: #8a97ad; background: #eef1f5; }

/* 风险与提醒 */
.risk-card { padding: 22px 24px; }
.risk-pill { margin-bottom: 14px; }
.risk-callout {
  background: #fef6e7;
  border-radius: 10px;
  padding: 16px 18px;
}
.risk-callout strong { display: block; font-size: 14px; color: #1f2d3d; line-height: 1.6; }
.risk-callout p { margin: 8px 0 12px; font-size: 12.5px; color: #8a6116; line-height: 1.7; }
h4 { font-size: 14px; color: #1f2d3d; margin: 24px 0 12px; }
.reminder-list { display: flex; flex-direction: column; gap: 10px; }
.reminder-row {
  display: flex;
  gap: 12px;
  align-items: baseline;
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12.5px;
}
.r-date { color: #93a0b4; flex-shrink: 0; }
.r-text { color: #3b4a5f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reminder-empty { color: #a8b3c5; font-size: 12.5px; }

@media (max-width: 1000px) {
  .progress-split { grid-template-columns: 1fr; }
  .stage-strip { flex-wrap: wrap; gap: 10px 0; }
  .sc-line { min-width: 12px; margin: 0 10px; }
}
</style>
