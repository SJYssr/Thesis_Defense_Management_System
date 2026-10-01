<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { dashboardApi, announcementApi } from '@/api/common'
import { progressApi } from '@/api/business'
import { useUserStore } from '@/stores/user'
import ProcessTimeline from '@/components/ProcessTimeline.vue'

const router = useRouter()
const userStore = useUserStore()
const data = ref<any>(null)
const progress = ref<any>(null)
const announcements = ref<any[]>([])

onMounted(async () => {
  data.value = (await dashboardApi.home()).data
  progress.value = (await progressApi.mine()).data
  announcements.value = (await announcementApi.list({ page: 1, page_size: 5 })).data.items
})
</script>

<template>
  <div class="student-home">
    <header class="student-heading"><h1>你好，{{ userStore.user?.real_name || '同学' }}</h1><p>按计划推进毕业设计，完成下一项任务</p></header>
    <section class="page-card home-topic">
      <div class="block-title">我的毕业设计<el-tag size="small">{{ data?.cards?.current_stage_cn || '待选题' }}</el-tag></div>
      <h2>{{ progress?.selection?.topic_title || progress?.topic_title || '尚未选择毕业设计题目' }}</h2>
      <p>指导教师：{{ progress?.teacher_name || '待确认' }}<span>当前阶段：{{ data?.cards?.current_stage_cn || '选题' }}</span><span v-if="data?.deadline_info?.deadline">下一节点：{{ data.deadline_info.deadline.slice(0,10) }}</span></p>
      <el-button v-if="!progress?.topic_title && !progress?.selection?.topic_title" link type="primary" @click="router.push('/student/topics')">前往题目中心选择题目</el-button>
    </section>
    <div class="home-steps"><ProcessTimeline v-if="progress?.timeline?.length" :timeline="progress.timeline" /><el-empty v-else description="选题后显示各阶段进度" :image-size="45" /></div>
    <div class="home-bottom">
      <section class="page-card"><h3 class="block-title">当前待办</h3><div v-for="(t,i) in data?.todos || []" :key="i" class="home-todo">{{ t }}</div><el-empty v-if="!data?.todos?.length" description="暂无待办事项" :image-size="60" /></section>
      <section class="page-card home-progress"><h3 class="block-title">我的进度</h3><el-progress type="circle" :percentage="data?.cards?.progress_percent || 0" :stroke-width="24" :width="190" color="#1878fd" /><el-button link type="primary" @click="router.push('/student/progress')">查看完整进度</el-button></section>
      <section class="page-card"><h3 class="block-title">AI 助手</h3><div class="assistant-tile"><h4>AI 模拟答辩</h4><p>基于论文内容进行答辩练习</p><el-button type="primary" @click="router.push('/student/ai-defense')">开始训练</el-button></div><div class="assistant-ppt"><h4>PPT 大纲生成</h4><p>生成答辩演示内容与结构</p><el-button @click="router.push('/student/ai-ppt')">生成</el-button></div></section>
    </div>
  </div>
</template>
<style scoped>
.home-topic h2 { margin: 8px 0; font-size: 23px; }
.home-topic .block-title { color: #788496; font-size: 14px; margin-bottom: 12px; }
.home-topic p { color: #9aa3b1; font-size: 12px; margin: 8px 0 0; }
.home-topic p span { margin-left: 22px; }
.home-steps { padding: 42px 10px 30px; }
.home-bottom { display: grid; grid-template-columns: 1.3fr 1fr 1fr; gap: 22px; }
.home-bottom > section { min-height: 335px; }
.home-todo { padding: 18px 14px; margin-bottom: 12px; background: #f8f9fc; border-radius: 5px; font-size: 13px; }
.home-progress :deep(.el-progress) { display: flex; justify-content: center; margin: 25px 0 18px; }
.home-progress > .el-button { display: block; margin: auto; }
.assistant-tile { padding: 20px; background: #edf4ff; border-radius: 6px; }
h4 { font-size: 14px; margin: 0 0 12px; } .assistant-tile h4 { color: #1878fd; }
.assistant-tile p,.assistant-ppt p { font-size: 12px; color: #939dab; line-height: 1.7; }
.assistant-ppt { position: relative; padding: 28px 0 0; } .assistant-ppt .el-button { position: absolute; right: 0; top: 24px; }
@media(max-width:1100px) { .home-bottom { grid-template-columns: 1fr; } .home-bottom > section { min-height: 0; } }
</style>
