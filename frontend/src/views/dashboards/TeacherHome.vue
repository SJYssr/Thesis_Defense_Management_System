<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { dashboardApi, announcementApi } from '@/api/common'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const data = ref<any>(null)
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
  const res = await dashboardApi.home()
  data.value = res.data
  const ann = await announcementApi.list({ page: 1, page_size: 5 })
  announcements.value = ann.data.items
})

const cards = [
  { key: 'topic_count', label: '我的题目', icon: 'Collection', color: '#1a76fd', bg: '#e8f1fd', path: '/teacher/topics' },
  { key: 'student_count', label: '指导学生', icon: 'User', color: '#4fc253', bg: '#e8f8e9', path: '/teacher/students' },
  { key: 'pending_selections', label: '待审选题', icon: 'Checked', color: '#e6a23c', bg: '#fdf3e3', path: '/teacher/selections' },
  { key: 'pending_opening', label: '待审开题', icon: 'Document', color: '#8e7cf5', bg: '#f1edfd', path: '/teacher/opening' },
  { key: 'pending_midterm', label: '待审中期', icon: 'DataLine', color: '#13c2c2', bg: '#e0f7f7', path: '/teacher/midterm' },
  { key: 'pending_final', label: '待审终期', icon: 'Medal', color: '#ff9f40', bg: '#fff2e5', path: '/teacher/final' },
  { key: 'pending_material', label: '待审材料', icon: 'FolderOpened', color: '#5e97e8', bg: '#e9f2fc', path: '/teacher/materials' },
  { key: 'risk_count', label: '风险学生', icon: 'WarningFilled', color: '#f56c6c', bg: '#fdeaea', path: '/teacher/students' },
]

const todos = computed(() => {
  if (!data.value) return []
  const c = data.value.cards
  const list: { label: string; count: number; path: string; tag: string }[] = [
    { label: '选题申请待审核', count: c.pending_selections, path: '/teacher/selections', tag: 'warning' },
    { label: '开题报告待审核', count: c.pending_opening, path: '/teacher/opening', tag: 'primary' },
    { label: '中期报告待审核', count: c.pending_midterm, path: '/teacher/midterm', tag: 'primary' },
    { label: '终期答辩待审核', count: c.pending_final, path: '/teacher/final', tag: 'warning' },
    { label: '毕业材料待审核', count: c.pending_material, path: '/teacher/materials', tag: 'primary' },
    { label: '风险学生需关注', count: c.risk_count, path: '/teacher/students', tag: 'danger' },
  ]
  return list.filter((t) => t.count > 0)
})
</script>

<template>
  <div v-if="data">
    <!-- 欢迎问候区 -->
    <div class="welcome-block" style="margin-bottom: 16px">
      <h2>{{ greeting }}，{{ userStore.user?.real_name }}老师</h2>
      <div class="sub">
        今天是 {{ todayStr }}，
        <template v-if="todos.length">您有 {{ todos.reduce((s: number, t: any) => s + t.count, 0) }} 个待办事项需要处理</template>
        <template v-else>暂无待办事项，一切顺利！</template>
      </div>
    </div>

    <!-- 8 张统计卡 -->
    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.key" :span="6" style="margin-bottom: 16px">
        <div class="page-card stat-card" style="cursor: pointer" @click="router.push(c.path)">
          <div class="stat-icon" :style="{ background: c.bg, color: c.color }">
            <el-icon :size="24"><component :is="c.icon" /></el-icon>
          </div>
          <div>
            <div class="value">{{ data.cards[c.key] ?? 0 }}</div>
            <div class="label">{{ c.label }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <!-- 待办审核 -->
      <el-col :span="16">
        <div class="page-card">
          <div class="block-title">
            待办审核
            <a class="more" @click="router.push('/teacher/selections')">全部待办 →</a>
          </div>
          <el-empty v-if="!todos.length" description="暂无待办，继续保持！" :image-size="80" />
          <div v-for="(t, i) in todos" :key="i" class="todo-item" @click="router.push(t.path)">
            <div class="todo-left">
              <el-tag :type="t.tag as any" size="small">{{ t.count }}</el-tag>
              <span class="todo-title">{{ t.label }}</span>
            </div>
            <el-button size="small" plain type="primary">去审核</el-button>
          </div>
        </div>
      </el-col>

      <!-- 最新公告 -->
      <el-col :span="8">
        <div class="page-card" style="height: 100%">
          <div class="block-title">
            最新公告
            <a class="more" @click="router.push('/announcements')">全部 →</a>
          </div>
          <el-empty v-if="!announcements.length" description="暂无公告" :image-size="70" />
          <div v-for="a in announcements" :key="a.id" class="ann-item">
            <span class="ann-title">{{ a.title }}</span>
            <span class="ann-time">{{ (a.publish_time || '').slice(5, 10) }}</span>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.todo-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 13px 6px;
  border-bottom: 1px dashed #ebeef5;
  cursor: pointer;
}

.todo-item:last-child {
  border-bottom: none;
}

.todo-item:hover .todo-title {
  color: #1a76fd;
}

.todo-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.todo-title {
  font-size: 14px;
}

.ann-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 2px;
  border-bottom: 1px dashed #ebeef5;
}

.ann-item:last-child {
  border-bottom: none;
}

.ann-title {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ann-time {
  color: #c0c4cc;
  font-size: 12px;
  flex-shrink: 0;
}
</style>
