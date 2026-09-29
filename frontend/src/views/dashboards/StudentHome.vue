<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { dashboardApi, announcementApi } from '@/api/common'
import { progressApi } from '@/api/business'
import { useUserStore } from '@/stores/user'
import ProcessTimeline from '@/components/ProcessTimeline.vue'
import StatusTag from '@/components/StatusTag.vue'

const router = useRouter()
const userStore = useUserStore()
const data = ref<any>(null)
const progress = ref<any>(null)
const announcements = ref<any[]>([])

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const todayStr = computed(() => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
})

onMounted(async () => {
  data.value = (await dashboardApi.home()).data
  progress.value = (await progressApi.mine()).data
  announcements.value = (await announcementApi.list({ page: 1, page_size: 5 })).data.items
})
</script>

<template>
  <div v-if="data">
    <!-- 欢迎问候区 -->
    <div class="welcome-block" style="margin-bottom: 16px">
      <h2>{{ greeting }}，{{ userStore.user?.real_name }}</h2>
      <div class="sub">今天是 {{ todayStr }}，继续加油！</div>
    </div>

    <!-- 我的题目 + 进度 -->
    <div class="page-card" style="margin-bottom: 16px">
      <div class="block-title">
        我的题目
        <a class="more" @click="router.push('/student/progress')">查看进度 →</a>
      </div>
      <template v-if="progress?.selection?.topic_title || progress?.topic_title">
        <div style="display: flex; justify-content: space-between; align-items: flex-start">
          <div style="flex: 1">
            <div style="font-size: 17px; font-weight: 600">{{ progress.selection?.topic_title || progress.topic_title }}</div>
            <div style="color: #8a97ad; font-size: 13px; margin: 8px 0 14px">
              指导教师: {{ progress.teacher_name || '-' }}
              <el-divider direction="vertical" />
              当前阶段: <el-tag size="small" type="primary">{{ data.cards.current_stage_cn }}</el-tag>
              <el-divider direction="vertical" />
              风险: <StatusTag :status="data.cards.risk_level" />
              <template v-if="data.deadline_info?.days_left != null">
                <el-divider direction="vertical" />
                <span :style="{ color: data.deadline_info.days_left < 7 ? '#f56c6c' : '#4fc253' }">
                  距截止 {{ data.deadline_info.days_left }} 天
                </span>
              </template>
            </div>
            <div style="display: flex; align-items: center; gap: 12px; max-width: 520px">
              <el-progress :percentage="data.cards.progress_percent" :stroke-width="10" style="flex: 1"
                           :color="data.cards.progress_percent >= 60 ? '#4fc253' : '#1a76fd'" />
              <span style="font-weight: 700; color: #1a76fd">{{ data.cards.progress_percent }}%</span>
            </div>
          </div>
        </div>
        <el-divider />
        <ProcessTimeline v-if="progress?.timeline" :timeline="progress.timeline" />
      </template>
      <el-empty v-else description="尚未选择毕业设计题目" :image-size="80">
        <el-button type="primary" @click="router.push('/student/topics')">前往题目中心选题</el-button>
      </el-empty>
    </div>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <!-- 待办事项 -->
      <el-col :span="14">
        <div class="page-card" style="height: 100%">
          <div class="block-title">待办事项</div>
          <el-empty v-if="!data.todos.length" description="暂无待办, 继续保持!" :image-size="70" />
          <div v-for="(t, i) in data.todos" :key="i" class="todo-item">
            <span class="todo-dot" />
            <span style="flex: 1">{{ t }}</span>
          </div>
        </div>
      </el-col>

      <!-- 系统公告 -->
      <el-col :span="10">
        <div class="page-card" style="height: 100%">
          <div class="block-title">
            系统公告
            <a class="more" @click="router.push('/announcements')">全部 →</a>
          </div>
          <el-empty v-if="!announcements.length" description="暂无公告" :image-size="70" />
          <div v-for="a in announcements" :key="a.id" class="ann-item" @click="router.push('/announcements')">
            <span class="ann-title">{{ a.title }}</span>
            <span class="ann-time">{{ (a.publish_time || '').slice(5, 10) }}</span>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- AI 助手横幅(渐变) -->
    <div class="ai-banner">
      <div class="ai-banner-left">
        <div class="ai-banner-title">
          <el-icon><MagicStick /></el-icon>
          AI 智能助手
        </div>
        <div class="ai-banner-desc">基于 RAG 的智能服务: 上传论文构建知识库后可进行 AI 模拟答辩、获得答辩报告与 PPT 大纲</div>
      </div>
      <div class="ai-banner-actions">
        <el-button round @click="router.push('/student/ai-chat')">知识问答</el-button>
        <el-button round type="primary" dark bg @click="router.push('/student/ai-defense')">开始模拟答辩</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.todo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 2px;
  border-bottom: 1px dashed #ebeef5;
  font-size: 14px;
}

.todo-item:last-child {
  border-bottom: none;
}

.todo-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #e6a23c;
  flex-shrink: 0;
}

.ann-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 2px;
  border-bottom: 1px dashed #ebeef5;
  cursor: pointer;
}

.ann-item:last-child {
  border-bottom: none;
}

.ann-item:hover .ann-title {
  color: #1a76fd;
}

.ann-title {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-right: 10px;
}

.ann-time {
  color: #c0c4cc;
  font-size: 12px;
  flex-shrink: 0;
}

.ai-banner {
  background: linear-gradient(100deg, #1a76fd 0%, #4a9bfc 100%);
  border-radius: 12px;
  padding: 22px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #fff;
  box-shadow: 0 8px 24px rgba(26, 118, 253, 0.25);
}

.ai-banner-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 8px;
}

.ai-banner-desc {
  font-size: 13px;
  opacity: 0.9;
  max-width: 560px;
}

.ai-banner-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}
</style>
