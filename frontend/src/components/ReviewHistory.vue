<script setup lang="ts">
defineProps<{
  records: { action: string; comment?: string | null; before_status?: string | null; after_status: string; reviewer_name?: string | null; created_at?: string | null }[]
}>()

const ACTION_CN: Record<string, string> = {
  SUBMIT: '提交', APPROVE: '审核通过', REJECT: '驳回', RETURN: '退回', RESUBMIT: '重新提交',
}

const TYPE: Record<string, string> = {
  SUBMIT: 'primary', APPROVE: 'success', REJECT: 'danger', RETURN: 'warning', RESUBMIT: 'primary',
}
</script>

<template>
  <el-empty v-if="!records.length" description="暂无审核记录" :image-size="60" />
  <el-timeline v-else>
    <el-timeline-item v-for="(r, i) in records" :key="i" :timestamp="(r.created_at || '').replace('T', ' ').slice(0, 16)" placement="top"
                      :type="(TYPE[r.action] || 'info') as any">
      <el-tag size="small" :type="(TYPE[r.action] || 'info') as any" style="margin-right: 8px">
        {{ ACTION_CN[r.action] || r.action }}
      </el-tag>
      <span v-if="r.reviewer_name" style="color: #606266; font-size: 13px">{{ r.reviewer_name }}</span>
      <div v-if="r.comment" style="color: #909399; font-size: 13px; margin-top: 4px">{{ r.comment }}</div>
    </el-timeline-item>
  </el-timeline>
</template>
