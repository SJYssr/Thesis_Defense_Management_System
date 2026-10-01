<script setup lang="ts">
import StatusTag from './StatusTag.vue'
import ReviewHistory from './ReviewHistory.vue'
defineProps<{ view: any; versions?: any[]; history?: any[] }>()
</script>
<template>
  <aside class="page-card material-status">
    <h3 class="ms-title">当前状态</h3>
    <div class="ms-status"><StatusTag :status="view?.status || 'DRAFT'" /></div>

    <h4>前置条件</h4>
    <span class="prereq" :class="view?.can_edit === false ? 'locked' : 'ok'">
      {{ view?.can_edit === false ? '材料已提交，不可编辑' : '前置审核已通过，可编辑提交' }}
    </span>

    <h4>版本记录</h4>
    <div class="version-list">
      <div v-for="v in versions || []" :key="v.id" class="version-row">
        V{{ v.version_no }} · {{ (v.submitted_at || '').replace('T', ' ').slice(0, 16) }}
      </div>
      <div v-if="!versions?.length" class="version-row muted">{{ view?.current_version ? `当前版本 V${view.current_version}` : '暂无提交版本' }}</div>
    </div>

    <h4>教师意见</h4>
    <div class="teacher-note" :class="{ empty: !view?.review_comment }">{{ view?.review_comment || '提交材料后，教师的审核意见将在此显示。' }}</div>

    <el-collapse v-if="history?.length" class="status-history"><el-collapse-item title="查看审核历史"><ReviewHistory :records="history" /></el-collapse-item></el-collapse>
  </aside>
</template>

<style scoped>
.material-status { padding: 22px 24px; }
.ms-title { font-size: 16px; color: #1f2d3d; margin: 0 0 12px; }
.ms-status :deep(.el-tag) { font-size: 12px; }
h4 { font-size: 14px; color: #1f2d3d; margin: 24px 0 12px; }
.prereq {
  display: inline-block;
  padding: 3px 12px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
}
.prereq.ok { color: #16a34a; background: #dcfce7; }
.prereq.locked { color: #8a97ad; background: #eef1f5; }
.version-list { display: flex; flex-direction: column; gap: 8px; }
.version-row {
  background: #f5f8fd;
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 12.5px;
  color: #3b4a5f;
}
.version-row.muted { color: #a8b3c5; }
.teacher-note {
  background: #fef6e7;
  border-radius: 8px;
  padding: 14px 16px;
  font-size: 13px;
  color: #8a6116;
  line-height: 1.8;
  white-space: pre-wrap;
}
.teacher-note.empty { color: #b8a37e; }
</style>
