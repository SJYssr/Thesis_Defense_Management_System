<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string; map?: Record<string, { label: string; type: string }> }>()

const DEFAULT_MAP: Record<string, { label: string; type: string }> = {
  // 通用阶段状态
  NOT_SUBMITTED: { label: '未提交', type: 'info' },
  DRAFT: { label: '草稿', type: 'info' },
  SUBMITTED: { label: '待审核', type: 'warning' },
  UNDER_REVIEW: { label: '审核中', type: 'warning' },
  APPROVED: { label: '已通过', type: 'success' },
  REJECTED: { label: '已驳回', type: 'danger' },
  RESUBMIT_REQUIRED: { label: '需重新提交', type: 'danger' },
  // 选题
  PENDING: { label: '待审核', type: 'warning' },
  CANCELLED: { label: '已撤销', type: 'info' },
  NOT_SELECTED: { label: '未选题', type: 'info' },
  // 题目
  PUBLISHED: { label: '已发布', type: 'success' },
  CLOSED: { label: '已关闭', type: 'info' },
  // 风险
  NORMAL: { label: '正常', type: 'success' },
  WARNING: { label: '警告', type: 'warning' },
  HIGH: { label: '高风险', type: 'danger' },
  // AI 文档
  WAITING: { label: '待处理', type: 'info' },
  PROCESSING: { label: '处理中', type: 'warning' },
  SUCCESS: { label: '已完成', type: 'success' },
  FAILED: { label: '失败', type: 'danger' },
  RUNNING: { label: '进行中', type: 'warning' },
  FINISHED: { label: '已结束', type: 'success' },
  CREATED: { label: '已创建', type: 'info' },
}

const conf = computed(() => (props.map || DEFAULT_MAP)[props.status] || { label: props.status, type: 'info' })
</script>

<template>
  <el-tag size="small" :type="conf.type as any">{{ conf.label }}</el-tag>
</template>
