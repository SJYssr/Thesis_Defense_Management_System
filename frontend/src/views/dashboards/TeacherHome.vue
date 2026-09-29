<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const teacherName = computed(() => `${userStore.user?.real_name || ''}老师`)

/* ===== 统计卡（5 张，按设计稿） ===== */
const statCards = [
  { key: 'topics', label: '我的题目', value: 8, icon: 'doc', tileBg: '#d7e9fd', iconColor: '#1878fd', sub: '已发布 <b>8</b>' },
  { key: 'students', label: '指导学生', value: 15, icon: 'users', tileBg: '#ddf6e7', iconColor: '#34c77b', sub: '进行中 <b class="g">12</b>' },
  { key: 'pending', label: '待审核任务', value: 5, icon: 'clipboard', tileBg: '#feefdc', iconColor: '#f59e0b', sub: '开题 <b class="o">2</b> 中期 <b class="o">2</b> 终期 <b class="o">1</b>' },
  { key: 'done', label: '已完成任务', value: 18, icon: 'check', tileBg: '#ece7fc', iconColor: '#8b5cf6', sub: '今年累计' },
  { key: 'notice', label: '系统公告', value: 3, icon: 'bell', tileBg: '#fde3e3', iconColor: '#e83a3a', sub: '未读 <b class="r">1</b>' },
]
const CARD_PATH: Record<string, string> = {
  topics: '/teacher/topics',
  students: '/teacher/students',
  pending: '/teacher/selections',
  done: '/teacher/home',
  notice: '/announcements',
}

const ICON_PATHS: Record<string, string> = {
  doc: '<path d="M6.5 2h7.1l5.9 5.9V20a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm6.6 1.8V9h5.2L13.1 3.8zM7.8 12.2h8.4a1 1 0 1 1 0 2H7.8a1 1 0 1 1 0-2zm0 4h8.4a1 1 0 1 1 0 2H7.8a1 1 0 1 1 0-2z"/>',
  users: '<circle cx="9" cy="7.8" r="3.3"/><path d="M3.3 19.2c.5-3.1 2.9-4.8 5.7-4.8s5.2 1.7 5.7 4.8c.1.6-.4 1.2-1 1.2H4.3c-.6 0-1.1-.6-1-1.2z"/><circle cx="16.6" cy="8.4" r="2.7"/><path d="M15.3 12.3c2.7.1 4.9 1.7 5.4 4.4.1.6-.4 1.2-1 1.2h-3.1c.4-1.7-.1-4-1.3-5.6z"/>',
  clipboard: '<path d="M9 2.5h6a1.5 1.5 0 0 1 1.5 1.5v.6H18A2 2 0 0 1 20 6.6v13A2.4 2.4 0 0 1 17.6 22H6.4A2.4 2.4 0 0 1 4 19.6v-13a2 2 0 0 1 2-2h1.5V4A1.5 1.5 0 0 1 9 2.5zm.3 6.3a1 1 0 1 0 0 2h5.4a1 1 0 1 0 0-2H9.3zm0 4.2a1 1 0 1 0 0 2h5.4a1 1 0 1 0 0-2H9.3zm0 4.2a1 1 0 1 0 0 2h3.2a1 1 0 1 0 0-2H9.3z"/>',
  check: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm5.1 7.6-6 6.4a1.1 1.1 0 0 1-1.6 0l-3.5-3.6a1.1 1.1 0 1 1 1.6-1.6l2.7 2.8 5.2-5.6a1.1 1.1 0 0 1 1.6 1.6z"/>',
  bell: '<path d="M12 2.5c-3.6 0-6.2 2.8-6.2 6.4v3.6L4.2 15c-.5.7 0 1.7.9 1.7h13.8c.9 0 1.4-1 .9-1.7l-1.6-2.5V8.9c0-3.6-2.6-6.4-6.2-6.4zM9.8 18.6a2.3 2.3 0 0 0 4.4 0H9.8z"/>',
  horn: '<path d="M20.5 4.6c0-.9-1-1.5-1.8-1L8.9 8.2H5.2A2.7 2.7 0 0 0 2.5 11v2.3a2.7 2.7 0 0 0 2.7 2.7h.9l1.1 4.4a1 1 0 0 0 1 .7h1a1 1 0 0 0 .96-1.24L9.4 16l9.3 4.7c.8.4 1.8-.1 1.8-1V4.6z"/>',
}

/* ===== 我的任务 ===== */
const taskTab = ref<'pending' | 'doing' | 'done'>('pending')
const TASK_TABS = [
  { key: 'pending' as const, label: '待处理', count: 5 },
  { key: 'doing' as const, label: '进行中', count: 12 },
  { key: 'done' as const, label: '已完成', count: 18 },
]

interface TaskRow {
  type: string
  student: string
  topic: string
  time: string
  status: string
  path: string
}

const PENDING_TASKS: TaskRow[] = [
  { type: '开题报告审核', student: '李明', topic: '基于深度学习的图像识别系统', time: '2024-05-18 10:20', status: '待审核', path: '/teacher/opening' },
  { type: '中期答辩审核', student: '王芳', topic: '基于SpringBoot的校园二手交易平台', time: '2024-05-17 14:30', status: '待审核', path: '/teacher/midterm' },
  { type: '开题报告审核', student: '张华', topic: '大学生心理健康管理系统', time: '2024-05-16 09:15', status: '待审核', path: '/teacher/opening' },
  { type: '终期答辩审核', student: '刘洋', topic: '基于Vue的在线教育平台', time: '2024-05-15 16:40', status: '待审核', path: '/teacher/final' },
  { type: '中期答辩审核', student: '陈丽', topic: '基于Python的数据可视化分析', time: '2024-05-14 11:25', status: '待审核', path: '/teacher/midterm' },
]
const DOING_TASKS: TaskRow[] = [
  { type: '选题指导', student: '赵磊', topic: '基于微服务架构的物流管理系统', time: '2024-05-12 09:00', status: '进行中', path: '/teacher/students' },
  { type: '开题指导', student: '孙悦', topic: '智能宿舍管理系统', time: '2024-05-10 15:30', status: '进行中', path: '/teacher/students' },
  { type: '中期指导', student: '周涛', topic: '基于区块链的成绩存证系统', time: '2024-05-08 11:20', status: '进行中', path: '/teacher/students' },
]
const DONE_TASKS: TaskRow[] = [
  { type: '选题审核', student: '吴倩', topic: '基于大数据的舆情分析平台', time: '2024-05-06 10:10', status: '已完成', path: '/teacher/selections' },
  { type: '开题报告审核', student: '郑凯', topic: '基于物联网的环境监测系统', time: '2024-05-04 14:00', status: '已完成', path: '/teacher/opening' },
  { type: '毕业材料审核', student: '冯雪', topic: '基于机器学习的推荐算法研究', time: '2024-05-02 16:45', status: '已完成', path: '/teacher/materials' },
]
const taskRows = computed(() => {
  if (taskTab.value === 'pending') return PENDING_TASKS
  if (taskTab.value === 'doing') return DOING_TASKS
  return DONE_TASKS
})

/* ===== 我的学生情况（环形图） ===== */
const donutRef = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
const STAGE_DATA = [
  { name: '选题中', value: 3, color: '#4a9eff' },
  { name: '开题阶段', value: 4, color: '#34c77b' },
  { name: '中期阶段', value: 4, color: '#f5a623' },
  { name: '终期阶段', value: 3, color: '#b18cf5' },
  { name: '已完成', value: 1, color: '#c3cad6' },
]

function renderDonut() {
  if (!donutRef.value) return
  if (!chart) chart = echarts.init(donutRef.value)
  chart.setOption({
    series: [
      {
        type: 'pie',
        radius: ['62%', '92%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: false,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 2 },
        data: STAGE_DATA.map((d) => ({ name: d.name, value: d.value, itemStyle: { color: d.color } })),
      },
    ],
  })
}
function resizeChart() {
  chart?.resize()
}

/* ===== 系统公告 ===== */
const ANNOUNCEMENTS = [
  { title: '关于2024届毕业设计工作安排的通知', tag: '重要', tagClass: 't-important', date: '2024-03-01' },
  { title: '毕业设计中期检查安排', tag: '通知', tagClass: 't-notice', date: '2024-02-20' },
  { title: '关于答辩论文格式要求的说明', tag: '通知', tagClass: 't-notice', date: '2024-02-15' },
  { title: '优秀毕业设计作品评选通知', tag: '通知', tagClass: 't-notice', date: '2024-01-28' },
  { title: '系统维护通知', tag: '一般', tagClass: 't-general', date: '2024-01-20' },
]

/* ===== 近期日程 ===== */
const SCHEDULES = [
  { date: '05-20', week: '今天', title: '中期答辩评审', time: '14:00 - 16:00', place: '腾讯会议' },
  { date: '05-22', week: '周二', title: '指导学生开题汇报', time: '10:00 - 12:00', place: '教学楼A301' },
  { date: '05-25', week: '周六', title: '毕业设计进度检查', time: '09:00 - 11:00', place: '线上' },
  { date: '05-28', week: '周二', title: '教研室会议', time: '15:00 - 17:00', place: '行政楼B201' },
]

onMounted(() => {
  renderDonut()
  window.addEventListener('resize', resizeChart)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="teacher-home">
    <!-- ===== 欢迎横幅 ===== -->
    <div class="banner">
      <div class="banner-text">
        <h1>{{ greeting }}，{{ teacherName }}!</h1>
        <p>欢迎使用毕业设计管理系统，祝您工作顺利！</p>
      </div>
      <!-- 提示板插画 -->
      <svg class="banner-illus" viewBox="0 0 420 130" preserveAspectRatio="xMaxYMax meet" aria-hidden="true">
        <!-- 绿叶 -->
        <g fill="#7fd6a4">
          <ellipse cx="30" cy="112" rx="10" ry="22" transform="rotate(-28 30 112)" />
          <ellipse cx="52" cy="118" rx="8" ry="18" transform="rotate(14 52 118)" />
          <ellipse cx="398" cy="112" rx="10" ry="22" transform="rotate(24 398 112)" />
        </g>
        <g fill="#a9e3c4">
          <ellipse cx="42" cy="104" rx="8" ry="18" transform="rotate(-8 42 104)" />
          <ellipse cx="408" cy="100" rx="8" ry="18" transform="rotate(-16 408 100)" />
        </g>
        <!-- 白板 -->
        <rect x="120" y="18" width="180" height="86" rx="8" fill="#ffffff" stroke="#cfe3fb" stroke-width="3" />
        <rect x="200" y="104" width="20" height="14" fill="#9db9dd" />
        <rect x="176" y="118" width="68" height="6" rx="3" fill="#b9d2ef" />
        <!-- 白板内容：学士帽 + 文本行 -->
        <path d="M172 40 210 30l38 10-38 10-38-10z" fill="#1878fd" />
        <path d="M186 46v9c0 4 8 7 24 7s24-3 24-7v-9l-24 8-24-8z" fill="#4a9eff" />
        <rect x="134" y="74" width="88" height="7" rx="3.5" fill="#d7e7fb" />
        <rect x="134" y="88" width="60" height="7" rx="3.5" fill="#e4effc" />
        <!-- 老师 -->
        <circle cx="332" cy="42" r="13" fill="#f0c8a0" />
        <path d="M318 38c0-9 7-14 14-14s14 5 14 14l-4-3c-3 2-8 3-10 3-3 0-6-1-8-3l-6 3z" fill="#3a4a5f" />
        <path d="M310 96c2-22 12-32 22-32s20 10 22 32l-8 22h-28l-8-22z" fill="#2f6fd6" />
        <path d="M322 66c4 6 16 6 20 0l4 30h-28l4-30z" fill="#ffffff" opacity=".92" />
        <path d="M306 74c-6 6-8 14-6 22l8-4c-2-6 0-12 4-16l-6-2zM358 74c6 6 8 14 6 22l-8-4c2-6 0-12-4-16l6-2z" fill="#2f6fd6" />
        <!-- 指向白板的手臂 -->
        <path d="M312 68C296 62 280 56 268 54l-2 8c12 3 26 8 40 14l6-8z" fill="#2f6fd6" />
        <circle cx="266" cy="56" r="5" fill="#f0c8a0" />
      </svg>
    </div>

    <!-- ===== 统计卡（5 张） ===== -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.key" class="stat-card" @click="router.push(CARD_PATH[c.key])">
        <div class="stat-icon" :style="{ background: c.tileBg, color: c.iconColor }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="ICON_PATHS[c.icon]" />
        </div>
        <div class="stat-info">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-sub" v-html="c.sub" />
        </div>
      </div>
    </div>

    <!-- ===== 我的任务 + 我的学生情况 ===== -->
    <div class="mid-row">
      <div class="card task-card">
        <div class="card-head">
          <span class="card-title"><i class="head-ic"><svg viewBox="0 0 24 24" fill="currentColor" v-html="ICON_PATHS.clipboard" /></i>我的任务</span>
        </div>
        <div class="task-tabs">
          <button
            v-for="t in TASK_TABS" :key="t.key"
            class="task-tab" :class="{ active: taskTab === t.key }"
            @click="taskTab = t.key"
          >
            {{ t.label }}（{{ t.count }}）
          </button>
        </div>
        <el-table :data="taskRows" class="task-table">
          <el-table-column type="index" label="序号" min-width="46" align="center" />
          <el-table-column prop="type" label="任务类型" min-width="100" />
          <el-table-column prop="student" label="学生姓名" min-width="86" />
          <el-table-column prop="topic" label="题目名称" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="topic-text">{{ row.topic }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="time" label="提交时间" min-width="130" />
          <el-table-column label="状态" min-width="78" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="row.status === '待审核' ? 's-pending' : row.status === '进行中' ? 's-doing' : 's-done'">{{ row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" min-width="66" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="router.push(row.path)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="card donut-card">
        <div class="card-head">
          <span class="card-title"><i class="head-ic"><svg viewBox="0 0 24 24" fill="currentColor" v-html="ICON_PATHS.users" /></i>我的学生情况</span>
          <a class="more-link" @click="router.push('/teacher/students')">查看更多<el-icon :size="12"><ArrowRight /></el-icon></a>
        </div>
        <div class="donut-wrap">
          <div class="donut-box">
            <div ref="donutRef" class="donut-chart" />
            <div class="donut-center">
              <div class="donut-num">15</div>
              <div class="donut-label">指导学生总数</div>
            </div>
          </div>
          <div class="donut-legend">
            <div v-for="d in STAGE_DATA" :key="d.name" class="legend-row">
              <span class="dot" :style="{ background: d.color }" />
              <span class="legend-name">{{ d.name }}</span>
              <span class="legend-val">{{ d.value }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 系统公告 + 近期日程 ===== -->
    <div class="bottom-row">
      <div class="card ann-card">
        <div class="card-head">
          <span class="card-title"><i class="head-ic"><svg viewBox="0 0 24 24" fill="currentColor" v-html="ICON_PATHS.bell" /></i>系统公告</span>
          <a class="more-link" @click="router.push('/announcements')">查看更多<el-icon :size="12"><ArrowRight /></el-icon></a>
        </div>
        <div class="ann-list">
          <div v-for="a in ANNOUNCEMENTS" :key="a.title" class="ann-row">
            <i class="horn-ic"><svg viewBox="0 0 24 24" fill="currentColor" v-html="ICON_PATHS.horn" /></i>
            <span class="ann-title">{{ a.title }}</span>
            <span class="ann-tag" :class="a.tagClass">{{ a.tag }}</span>
            <span class="ann-date">{{ a.date }}</span>
          </div>
        </div>
      </div>

      <div class="card sched-card">
        <div class="card-head">
          <span class="card-title"><i class="head-ic"><svg viewBox="0 0 24 24" fill="currentColor" v-html="ICON_PATHS.doc" /></i>近期日程</span>
          <a class="more-link" @click="router.push('/teacher/home')">查看更多<el-icon :size="12"><ArrowRight /></el-icon></a>
        </div>
        <div class="sched-list">
          <div v-for="(s, i) in SCHEDULES" :key="s.date" class="sched-row">
            <div class="date-box">
              <div class="d1">{{ s.date }}</div>
              <div class="d2">{{ s.week }}</div>
            </div>
            <div class="tl">
              <span class="tl-dot" />
              <span v-if="i < SCHEDULES.length - 1" class="tl-line" />
            </div>
            <div class="sched-title">{{ s.title }}</div>
            <div class="sched-meta">
              <el-icon :size="13"><Clock /></el-icon>
              <span>{{ s.time }}</span>
            </div>
            <div class="sched-meta">
              <el-icon :size="13"><Location /></el-icon>
              <span>{{ s.place }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 欢迎横幅 ===== */
.banner {
  position: relative;
  border-radius: 12px;
  background: linear-gradient(96deg, #dcecfe 0%, #cfe6fd 55%, #c4ddfc 100%);
  min-height: 120px;
  padding: 26px 36px;
  display: flex;
  align-items: center;
  overflow: hidden;
  margin-bottom: 14px;
}
.banner-text h1 {
  font-size: 27px;
  font-weight: 700;
  color: #17293f;
  letter-spacing: 1px;
}
.banner-text p {
  margin-top: 10px;
  font-size: 15px;
  color: #5a6f8a;
}
.banner-illus {
  position: absolute;
  right: 26px;
  bottom: 0;
  height: 118px;
  width: 400px;
}

/* ===== 统计卡（5 张） ===== */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
  margin-bottom: 14px;
}
.stat-card {
  background: #fff;
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 104px;
  cursor: pointer;
}
.stat-icon {
  width: 54px;
  height: 54px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-icon svg { width: 28px; height: 28px; }
.stat-info { flex: 1; min-width: 0; }
.stat-label {
  font-size: 14px;
  color: #5a6f8a;
}
.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  line-height: 1.15;
  margin-top: 3px;
}
.stat-sub {
  font-size: 12.5px;
  color: #8a97ad;
  margin-top: 4px;
  white-space: nowrap;
}
.stat-sub :deep(b) { font-weight: 600; color: #3b4a5f; }
.stat-sub :deep(b.g) { color: #27ae60; }
.stat-sub :deep(b.o) { color: #e67e22; }
.stat-sub :deep(b.r) { color: #e83a3a; }

/* ===== 卡片通用 ===== */
.mid-row {
  display: grid;
  grid-template-columns: 1fr 460px;
  gap: 14px;
  margin-bottom: 14px;
}
.bottom-row {
  display: grid;
  grid-template-columns: 1fr 1.05fr;
  gap: 14px;
}
.card {
  background: #fff;
  border-radius: 10px;
  padding: 14px 18px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15.5px;
  font-weight: 700;
  color: #1f2d3d;
}
.head-ic {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: #1878fd;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.head-ic svg { width: 13px; height: 13px; }
.more-link {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 13px;
  color: #1878fd;
  cursor: pointer;
}

/* ===== 我的任务 ===== */
.task-tabs {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.task-tab {
  border: none;
  outline: none;
  cursor: pointer;
  padding: 7px 16px;
  border-radius: 6px;
  font-size: 13.5px;
  background: #f3f5f8;
  color: #5a6f8a;
}
.task-tab.active {
  background: #e8f1fd;
  color: #1878fd;
  font-weight: 600;
}
.task-table :deep(thead th) {
  background: #eaf3fe;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 13px;
  padding: 8px 0;
  border-bottom: none;
}
.task-table :deep(thead th .cell) { white-space: nowrap; }
.task-table :deep(td .cell) { white-space: nowrap; }
.task-table :deep(td) {
  font-size: 13px;
  color: #3b4a5f;
  padding: 8.5px 0;
}
.task-table :deep(.cell) { padding: 0 8px; }
.task-table :deep(.el-button.is-link) { font-size: 13px; }
.topic-text {
  color: #8a97ad;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
.status-tag {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  line-height: 18px;
}
.s-pending { color: #e67e22; background: #fdf1e3; }
.s-doing { color: #1878fd; background: #e3effe; }
.s-done { color: #27ae60; background: #e4f7ec; }

/* ===== 我的学生情况 ===== */
.donut-wrap {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 6px 4px 2px;
}
.donut-box {
  position: relative;
  width: 210px;
  height: 210px;
  flex-shrink: 0;
}
.donut-chart { width: 100%; height: 100%; }
.donut-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.donut-num {
  font-size: 34px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  line-height: 1.1;
}
.donut-label {
  font-size: 12.5px;
  color: #8a97ad;
  margin-top: 4px;
}
.donut-legend { flex: 1; display: flex; flex-direction: column; gap: 16px; }
.legend-row {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 13.5px;
  color: #3b4a5f;
}
.legend-row .dot { width: 9px; height: 9px; border-radius: 50%; }
.legend-name { flex: 1; }
.legend-val { font-weight: 600; color: #1f2d3d; }

/* ===== 系统公告 ===== */
.ann-list { display: flex; flex-direction: column; }
.ann-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13.5px 2px;
}
.ann-row + .ann-row { border-top: 1px solid #f2f5f9; }
.horn-ic { color: #1878fd; display: inline-flex; }
.horn-ic svg { width: 16px; height: 16px; }
.ann-title {
  flex: 1;
  font-size: 13.5px;
  color: #2c3e5d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ann-tag {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 18px;
}
.t-important { color: #e74c3c; background: #fdeaea; }
.t-notice { color: #1878fd; background: #e3effe; }
.t-general { color: #27ae60; background: #e4f7ec; }
.ann-date {
  flex-shrink: 0;
  width: 86px;
  text-align: right;
  font-size: 12.5px;
  color: #97a3b4;
}

/* ===== 近期日程 ===== */
.sched-list { display: flex; flex-direction: column; }
.sched-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 9px 0;
}
.date-box {
  width: 64px;
  flex-shrink: 0;
  background: #f2f6fc;
  border-radius: 8px;
  text-align: center;
  padding: 7px 0;
}
.date-box .d1 {
  font-size: 14.5px;
  font-weight: 700;
  color: #1f2d3d;
}
.date-box .d2 {
  font-size: 11.5px;
  color: #8a97ad;
  margin-top: 2px;
}
.tl {
  position: relative;
  align-self: stretch;
  width: 10px;
  flex-shrink: 0;
  display: flex;
  justify-content: center;
}
.tl-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #1878fd;
  margin-top: 4px;
  position: relative;
  z-index: 1;
}
.tl-line {
  position: absolute;
  top: 14px;
  bottom: -14px;
  width: 2px;
  background: #e3ebf5;
}
.sched-title {
  width: 150px;
  flex-shrink: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: #2c3e5d;
}
.sched-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  color: #8a97ad;
}
</style>
