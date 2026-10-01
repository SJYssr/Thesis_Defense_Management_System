<script setup lang="ts">
import { onMounted, ref } from 'vue'
const props = withDefaults(defineProps<{ loader: (status: string) => Promise<any>; title?: string; statuses?: string[]; labels?: string[] }>(), { title: '提交总数', statuses: () => ['', 'APPROVED', 'SUBMITTED', 'REJECTED'], labels: () => ['提交总数', '已通过', '待审核', '未通过'] })
const counts = ref<(number | null)[]>([null, null, null, null])
const icons = ['Document', 'CircleCheckFilled', 'Clock', 'CircleCloseFilled']
onMounted(async () => { await Promise.allSettled(props.statuses.map(async (status, i) => { const res = await props.loader(status); counts.value[i] = res.data.total ?? res.data.items?.length ?? 0 })) })
</script>
<template>
  <div class="overview-stats">
    <article v-for="(label,i) in labels" :key="label" class="overview-stat" :class="`tone-${i}`">
      <div class="overview-icon"><el-icon><component :is="icons[i]" /></el-icon></div>
      <div><div class="overview-label">{{ i === 0 ? title : label }}</div><strong>{{ counts[i] ?? '—' }}</strong><small>{{ i === 0 ? '全部记录' : '占比 ' + (counts[0] ? Math.round((counts[i] || 0) / counts[0]! * 100) + '%' : '—') }}</small></div>
      <el-progress v-if="i > 0 && counts[0]" type="circle" :width="48" :stroke-width="4" :percentage="Math.round((counts[i] || 0) / counts[0]! * 100)" :color="['#087cff','#00bc8b','#ffab35','#ff4965'][i]" />
    </article>
  </div>
</template>
<style scoped>
.overview-stats { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 14px; margin-bottom: 16px; }
.overview-stat { display: flex; gap: 18px; align-items: center; background: white; padding: 20px; border-radius: 7px; min-height: 112px; }
.overview-icon { width: 60px; height: 60px; border-radius: 9px; background: #e1f2ff; color: #087cff; display: grid; place-items: center; flex-shrink: 0; font-size: 32px; }
.tone-1 .overview-icon { background: #ddfaf0; color: #00bc8b; }.tone-2 .overview-icon { background: #fff2e0; color: #ffab35; }.tone-3 .overview-icon { background: #ffe9ee; color: #ff4965; }
.overview-label { font-size: 13px; color: #637695; }.overview-stat strong { display: block; font-size: 29px; color: #101c38; line-height: 1.4; }.overview-stat small { font-size: 11px; color: #8b9ab0; }
.overview-stat :deep(.el-progress) { margin-left: auto; }.overview-stat :deep(.el-progress__text) { font-size: 11px !important; }
@media(max-width:1300px) { .overview-stat { padding: 15px; gap: 10px; }.overview-icon { width: 44px; height: 44px; font-size: 26px; }.overview-stat :deep(.el-progress) { display: none; } }
@media(max-width:800px) { .overview-stats { grid-template-columns: repeat(2,minmax(0,1fr)); } }
</style>
