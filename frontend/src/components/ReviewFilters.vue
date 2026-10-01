<script setup lang="ts">
import type { ReviewQuery } from '@/utils/reviewList'
const props = withDefaults(defineProps<{ query: ReviewQuery; pending?: string; rejected?: string }>(), { pending: 'SUBMITTED', rejected: 'REJECTED' })
const emit = defineEmits<{ search: [] }>()
function search() { props.query.page = 1; emit('search') }
function reset() { Object.assign(props.query, { page: 1, status: '', keyword: '', studentNo: '', topic: '', dates: [] }); emit('search') }
</script>
<template>
  <div class="review-filters">
    <label>学生姓名<el-input v-model="query.keyword" placeholder="请输入学生姓名" clearable @keyup.enter="search" /></label>
    <label>学号<el-input v-model="query.studentNo" placeholder="请输入学号" clearable @keyup.enter="search" /></label>
    <label>题目名称<el-input v-model="query.topic" placeholder="请输入题目名称" clearable @keyup.enter="search" /></label>
    <label>审核状态<el-select v-model="query.status"><el-option label="全部状态" value="" /><el-option label="待审核" :value="pending" /><el-option label="已通过" value="APPROVED" /><el-option label="未通过" :value="rejected" /></el-select></label>
    <label class="review-date">提交时间<el-date-picker v-model="query.dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" range-separator="至" /></label>
    <div class="review-search"><el-button type="primary" icon="Search" @click="search">查询</el-button><el-button icon="Refresh" @click="reset">重置</el-button></div>
  </div>
</template>
<style scoped>
.review-filters { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px 24px; margin-bottom:22px; padding-bottom:20px; border-bottom:1px solid #eef2f8; }.review-filters label { display:flex; align-items:center; gap:12px; font-size:12px; white-space:nowrap; font-weight:600; color:#435777; }.review-filters label > :deep(.el-input),.review-filters label > :deep(.el-select) { flex:1; min-width:0; }.review-date :deep(.el-date-editor) { width:100%; min-width:0; }.review-search { display:flex; justify-content:flex-end; gap:6px; }
@media(max-width:600px) { .review-filters { grid-template-columns:1fr; } }
</style>
