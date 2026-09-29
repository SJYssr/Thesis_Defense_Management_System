<script setup lang="ts">
const props = defineProps<{
  timeline: { stage: string; stage_cn: string; status: string; status_cn: string; done: boolean }[]
}>()

function stepStatus(item: { status: string; done: boolean }) {
  if (item.status === 'APPROVED' || item.done) return 'finish'
  if (item.status === 'SUBMITTED' || item.status === 'PENDING') return 'process'
  if (item.status === 'REJECTED' || item.status === 'RESUBMIT_REQUIRED') return 'error'
  return 'wait'
}
</script>

<template>
  <el-steps :active="timeline.filter((t) => t.done).length" align-center finish-status="success">
    <el-step v-for="item in timeline" :key="item.stage" :title="item.stage_cn"
             :description="item.status_cn" :status="stepStatus(item) as any" />
  </el-steps>
</template>
