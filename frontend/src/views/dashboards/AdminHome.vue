<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { STAT_ICONS } from '@/components/statIcons'
import { dashboardApi } from '@/api/common'

const router = useRouter()

/* ================= 欢迎横幅 ================= */
const slides = [
  {
    title: '欢迎使用毕业设计管理系统',
    sub: '规范流程 · 提高效率 · 智能辅助 · 助力毕业',
    art1: '以创新致未来',
    art2: '用实践铸成长',
  },
  {
    title: 'AI 智能辅助，毕设更高效',
    sub: '模拟答辩 · 知识问答 · 进度分析 · PPT 大纲',
    art1: '以智能提质量',
    art2: '以数据促成长',
  },
  {
    title: '全流程毕业设计管理',
    sub: '选题 · 开题 · 中期 · 终期 · 毕业材料',
    art1: '以规范立流程',
    art2: '以协同促高效',
  },
]
const slideIndex = ref(0)
let slideTimer: number | undefined

function switchSlide(i: number) {
  slideIndex.value = i
}

/* ================= 实时业务数据 ================= */
interface NamedValue { name: string; value: number }
interface Todo { text: string; cat: string; date: string; kind: string; dot: string }
interface Submission { name: string; content: string; time: string; status: string }
const cards = reactive({ student_count: 0, teacher_count: 0, topic_count: 0,
  selected_count: 0, completed_count: 0, pending_count: 0 })
const stageDistribution = ref<NamedValue[]>([])
const passRates = ref<NamedValue[]>([])
const announcements = ref<{ tag: string; title: string; date: string }[]>([])
const todos = ref<Todo[]>([])
const submissions = ref<Submission[]>([])
const pendingCounts = ref<{ label: string; count: number }[]>([])
const palette = ['#b8c4d4', '#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6']
const dotColors: Record<string, string> = {
  选题审核: '#f59e0b', 开题审核: '#3b82f6', 中期审核: '#13c2c2',
  终期审核: '#8b5cf6', 毕业材料: '#ef4444',
}
function pct(value: number, total: number) { return total ? Math.round(value / total * 100) : 0 }
const statCards = computed(() => [
  { label: '学生总数', value: cards.student_count, icon: 'users', color: '#2563eb', bg: '#d7f0fe' },
  { label: '教师总数', value: cards.teacher_count, icon: 'person', color: '#22c55e', bg: '#ddfef0' },
  { label: '题目总数', value: cards.topic_count, icon: 'doc', color: '#8b5cf6', bg: '#edeafd' },
  { label: '已选题人数', value: cards.selected_count, icon: 'case', color: '#f59e0b', bg: '#fef5ea',
    ring: pct(cards.selected_count, cards.student_count), ringColor: '#f59e0b' },
  { label: '材料审核完成', value: cards.completed_count, icon: 'cap', color: '#ef4444', bg: '#fee7ed',
    ring: pct(cards.completed_count, cards.student_count), ringColor: '#ef4444' },
  { label: '待审核事项', value: cards.pending_count, icon: 'bars', color: '#3b82f6', bg: '#dcf9fe' },
])
const barRef = ref<HTMLElement>()
const lineRef = ref<HTMLElement>()
const pieRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []
function makeChart(el: HTMLElement | undefined) {
  if (!el) return null
  const chart = echarts.init(el)
  charts.push(chart)
  return chart
}
function renderCharts() {
  const names = stageDistribution.value.map(item => item.name)
  const values = stageDistribution.value.map(item => item.value)
  makeChart(barRef.value)?.setOption({
    grid: { left: 50, right: 18, top: 30, bottom: 42 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: names, axisLabel: { interval: 0, rotate: names.length > 5 ? 20 : 0, fontSize: 10 } },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{ type: 'bar', barMaxWidth: 26, data: values.map((value, i) => ({ value,
      itemStyle: { color: palette[i % palette.length], borderRadius: [4, 4, 0, 0] } })) }],
  })
  makeChart(lineRef.value)?.setOption({
    grid: { left: 45, right: 18, top: 35, bottom: 38 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: passRates.value.map(item => item.name) },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{ type: 'line', smooth: true, data: passRates.value.map(item => item.value),
      itemStyle: { color: '#3b82f6' }, label: { show: true, position: 'top', formatter: '{c}%' } }],
  })
  makeChart(pieRef.value)?.setOption({
    title: { text: String(cards.student_count), subtext: '学生总数', left: 'center', top: '32%',
      textStyle: { fontSize: 28, fontWeight: 700 }, subTextStyle: { fontSize: 12 } },
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8,
      textStyle: { fontSize: 10 }, formatter: (name: string) => {
        const item = stageDistribution.value.find(entry => entry.name === name)
        return `${name} ${item?.value || 0} (${pct(item?.value || 0, cards.student_count)}%)`
      } },
    series: [{ type: 'pie', radius: ['52%', '72%'], center: ['50%', '42%'], label: { show: false },
      data: stageDistribution.value.map((item, i) => ({ ...item,
        itemStyle: { color: palette[i % palette.length] } })) }],
  })
}
function resizeCharts() { charts.forEach(chart => chart.resize()) }

const ANN_TAG: Record<string, { label: string; color: string; bg: string }> = {
  IMPORTANT: { label: '重要', color: '#f0564d', bg: '#fdecec' },
  NOTICE: { label: '通知', color: '#1878fd', bg: '#e8f1fd' },
  NORMAL: { label: '一般', color: '#e6822a', bg: '#fdf1e5' },
  URGENT: { label: '紧急', color: '#f0564d', bg: '#fdecec' },
}
const STATUS_PILL: Record<string, { color: string; bg: string }> = {
  待审核: { color: '#e6822a', bg: '#fdf3e6' },
  审核中: { color: '#1878fd', bg: '#e8f1fd' },
  已通过: { color: '#3fb950', bg: '#e9f9ee' },
  已退回: { color: '#f0564d', bg: '#fdecec' },
}
const todoChips = computed(() => [{ label: '全部', count: pendingCounts.value.reduce((sum, item) => sum + item.count, 0) },
  ...pendingCounts.value])
const activeChip = ref('全部')
const filteredTodos = computed(() => activeChip.value === '全部' ? todos.value :
  todos.value.filter(todo => todo.cat === activeChip.value))
function pickChip(label: string) { activeChip.value = label }

onMounted(async () => {
  const [home, analytics, distribution, activity] = await Promise.all([
    dashboardApi.home(), dashboardApi.analytics(), dashboardApi.stats(), dashboardApi.adminActivity(),
  ])
  Object.assign(cards, analytics.data.cards)
  passRates.value = analytics.data.pass_rates
  stageDistribution.value = distribution.data.stage_distribution
  announcements.value = home.data.announcements.map((item: any) => ({
    tag: item.priority || 'NORMAL', title: item.title, date: item.publish_time?.slice(0, 10) || '',
  }))
  pendingCounts.value = activity.data.counts
  todos.value = activity.data.todos.map((item: Todo) => ({ ...item, dot: dotColors[item.cat] || '#8b5cf6' }))
  submissions.value = activity.data.submissions
  await nextTick()
  renderCharts()
  window.addEventListener('resize', resizeCharts)
  slideTimer = window.setInterval(() => { slideIndex.value = (slideIndex.value + 1) % slides.length }, 5000)
})
onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  if (slideTimer) window.clearInterval(slideTimer)
  charts.forEach(chart => chart.dispose())
  charts = []
})
</script>

<template>
  <div class="admin-home">
    <!-- ===== 欢迎横幅 ===== -->
    <div class="banner">
      <transition name="banner-fade" mode="out-in">
        <div :key="slideIndex" class="banner-inner">
          <div class="banner-text">
            <h1>{{ slides[slideIndex].title }}</h1>
            <p>{{ slides[slideIndex].sub }}</p>
          </div>
          <div class="banner-art">
            <span>{{ slides[slideIndex].art1 }}</span>
            <span>{{ slides[slideIndex].art2 }}</span>
          </div>
        </div>
      </transition>
      <!-- 校园插画 -->
      <svg class="banner-illus" viewBox="0 0 560 170" preserveAspectRatio="xMaxYMax slice" aria-hidden="true">
        <!-- 远处教学楼 -->
        <g>
          <rect x="18" y="52" width="86" height="118" rx="3" fill="#c3dbfb" />
          <rect x="18" y="52" width="86" height="7" rx="3" fill="#a8ccf7" />
          <g fill="#e9f4ff">
            <rect v-for="c in 3" :key="'fa' + c" :x="28 + (c - 1) * 26" y="68" width="16" height="12" rx="1.5" />
            <rect v-for="c in 3" :key="'fb' + c" :x="28 + (c - 1) * 26" y="88" width="16" height="12" rx="1.5" />
            <rect v-for="c in 3" :key="'fc' + c" :x="28 + (c - 1) * 26" y="108" width="16" height="12" rx="1.5" />
            <rect v-for="c in 3" :key="'fd' + c" :x="28 + (c - 1) * 26" y="128" width="16" height="12" rx="1.5" />
          </g>
          <rect x="46" y="148" width="30" height="22" rx="2" fill="#9fc7f4" />
        </g>
        <!-- 主教学楼 -->
        <g>
          <rect x="128" y="16" width="168" height="154" rx="4" fill="#b7d5fa" />
          <rect x="122" y="10" width="180" height="10" rx="4" fill="#8fbdf5" />
          <g fill="#e9f4ff">
            <rect v-for="c in 4" :key="'ma' + c" :x="140 + (c - 1) * 38" y="30" width="26" height="18" rx="2" />
            <rect v-for="c in 4" :key="'mb' + c" :x="140 + (c - 1) * 38" y="56" width="26" height="18" rx="2" />
            <rect v-for="c in 4" :key="'mc' + c" :x="140 + (c - 1) * 38" y="82" width="26" height="18" rx="2" />
            <rect v-for="c in 4" :key="'md' + c" :x="140 + (c - 1) * 38" y="108" width="26" height="18" rx="2" />
          </g>
          <rect x="188" y="132" width="48" height="38" rx="3" fill="#8fbdf5" />
          <rect x="194" y="138" width="36" height="32" rx="2" fill="#7fb1f2" />
          <circle cx="212" cy="26" r="4" fill="#fff" opacity=".9" />
        </g>
        <!-- 侧楼 -->
        <g>
          <rect x="304" y="76" width="104" height="94" rx="4" fill="#cbe0fb" />
          <rect x="304" y="76" width="104" height="8" rx="4" fill="#a8ccf7" />
          <g fill="#e9f4ff">
            <rect v-for="c in 3" :key="'sa' + c" :x="314 + (c - 1) * 32" y="92" width="22" height="14" rx="2" />
            <rect v-for="c in 3" :key="'sb' + c" :x="314 + (c - 1) * 32" y="114" width="22" height="14" rx="2" />
          </g>
          <rect x="336" y="138" width="40" height="32" rx="3" fill="#9fc7f4" />
        </g>
        <!-- 树木与灌木 -->
        <g>
          <rect x="428" y="126" width="5" height="40" rx="2" fill="#b5836a" />
          <circle cx="430.5" cy="118" r="22" fill="#7ecfa9" />
          <circle cx="418" cy="130" r="13" fill="#5eb98c" />
          <circle cx="443" cy="132" r="11" fill="#93dab8" />
          <rect x="468" y="136" width="4" height="30" rx="2" fill="#b5836a" />
          <circle cx="470" cy="130" r="16" fill="#6cc79a" />
          <circle cx="508" cy="152" r="12" fill="#8ed6b2" />
          <circle cx="530" cy="156" r="10" fill="#a4e0c2" />
          <circle cx="488" cy="158" r="9" fill="#93dab8" />
        </g>
        <!-- 地面 -->
        <rect x="0" y="164" width="560" height="6" fill="#dcf0fe" />
      </svg>
      <!-- 轮播指示点 -->
      <div class="banner-dots">
        <span
          v-for="(_, i) in slides"
          :key="i"
          class="dot"
          :class="{ active: slideIndex === i }"
          @click="switchSlide(i)"
        />
      </div>
    </div>

    <!-- ===== 统计卡片 ===== -->
    <el-row :gutter="16" class="stat-row">
      <el-col v-for="c in statCards" :key="c.label" :span="4">
        <div class="stat-card">
          <div class="stat-icon" :style="{ background: c.bg, color: c.color }">
            <svg viewBox="0 0 24 24" fill="currentColor" v-html="STAT_ICONS[c.icon]" />
          </div>
          <div class="stat-info">
            <div class="value">{{ c.value }}</div>
            <div class="label">{{ c.label }}</div>
          </div>
          <div v-if="c.ring !== undefined" class="ring" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
            <span>{{ c.ring }}%</span>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- ===== 图表区 ===== -->
    <el-row :gutter="16" class="chart-row">
      <el-col :span="8">
        <div class="page-card chart-card">
          <div class="card-head">
            <div class="card-title"><i />各阶段学生人数统计</div>
            <span class="batch-select">全部学年</span>
          </div>
          <div ref="barRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="page-card chart-card">
          <div class="card-head">
            <div class="card-title"><i />各阶段通过率</div>
            <span class="batch-select">全部学年</span>
          </div>
          <div ref="lineRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="page-card chart-card">
          <div class="card-head">
            <div class="card-title"><i />学生进度分布</div>
            <span class="batch-select">全部学年</span>
          </div>
          <div ref="pieRef" class="chart-box" />
        </div>
      </el-col>
    </el-row>

    <!-- ===== 列表区 ===== -->
    <el-row :gutter="16" class="list-row">
      <!-- 系统公告 -->
      <el-col :span="8">
        <div class="page-card list-card">
          <div class="card-head">
            <div class="card-title">
              <svg viewBox="0 0 24 24" fill="currentColor" class="title-icon">
                <path d="M20.5 4.6c0-.9-1-1.5-1.8-1L8.9 8.2H5.2A2.7 2.7 0 0 0 2.5 11v2.3a2.7 2.7 0 0 0 2.7 2.7h.9l1.1 4.4a1 1 0 0 0 1 .7h1a1 1 0 0 0 .96-1.24L9.4 16l9.3 4.7c.8.4 1.8-.1 1.8-1V4.6z" />
              </svg>
              系统公告
            </div>
            <a class="more" @click="router.push('/announcements')">更多 ></a>
          </div>
          <div v-for="(a, i) in announcements" :key="i" class="ann-item" @click="router.push('/announcements')">
            <span class="ann-tag" :style="{ color: (ANN_TAG[a.tag] || ANN_TAG.NORMAL).color, background: (ANN_TAG[a.tag] || ANN_TAG.NORMAL).bg }">{{ (ANN_TAG[a.tag] || ANN_TAG.NORMAL).label }}</span>
            <span class="ann-title">{{ a.title }}</span>
            <span class="ann-date">{{ a.date }}</span>
          </div>
          <el-empty v-if="!announcements.length" description="暂无公告" :image-size="60" />
        </div>
      </el-col>

      <!-- 待办事项 -->
      <el-col :span="8">
        <div class="page-card list-card">
          <div class="card-head">
            <div class="card-title">
              <svg viewBox="0 0 24 24" fill="currentColor" class="title-icon">
                <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.1 14.2-3.9-3.9 1.5-1.5 2.4 2.4 5.2-5.2 1.5 1.5-6.7 6.7z" />
              </svg>
              待办事项
            </div>
            <a class="more" @click="router.push('/admin/selections')">更多 ></a>
          </div>
          <div class="todo-chips">
            <span
              v-for="chip in todoChips"
              :key="chip.label"
              class="chip"
              :class="{ active: activeChip === chip.label }"
              @click="pickChip(chip.label)"
            >
              {{ chip.label }}({{ chip.count }})
            </span>
          </div>
          <div v-for="(t, i) in filteredTodos" :key="i" class="todo-item">
            <span class="todo-dot" :style="{ background: t.dot }" />
            <span class="todo-text">{{ t.text }}</span>
            <span class="todo-cat">{{ t.cat }}</span>
            <span class="todo-date">{{ t.date }}</span>
          </div>
          <el-empty v-if="!filteredTodos.length" description="暂无待办" :image-size="60" />
        </div>
      </el-col>

      <!-- 最新提交记录 -->
      <el-col :span="8">
        <div class="page-card list-card">
          <div class="card-head">
            <div class="card-title">
              <svg viewBox="0 0 24 24" fill="currentColor" class="title-icon">
                <path d="M6.5 2h7.1l5.9 5.9V20a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm6.6 1.8V9h5.2L13.1 3.8zM7.8 12.2h8.4a1 1 0 1 1 0 2H7.8a1 1 0 1 1 0-2zm0 4h8.4a1 1 0 1 1 0 2H7.8a1 1 0 1 1 0-2z" />
              </svg>
              最新提交记录
            </div>
            <a class="more" @click="router.push('/admin/progress')">更多 ></a>
          </div>
          <div class="mini-table">
            <div class="mini-thead">
              <span class="col-name">学生姓名</span>
              <span class="col-content">提交内容</span>
              <span class="col-time">提交时间</span>
              <span class="col-status">状态</span>
            </div>
            <div v-for="(r, i) in submissions" :key="i" class="mini-tr">
              <span class="col-name">{{ r.name }}</span>
              <span class="col-content">{{ r.content }}</span>
              <span class="col-time">{{ r.time }}</span>
              <span class="col-status">
                <span class="status-pill" :style="{ color: (STATUS_PILL[r.status] || STATUS_PILL.待审核).color, background: (STATUS_PILL[r.status] || STATUS_PILL.待审核).bg }">{{ r.status }}</span>
              </span>
            </div>
          </div>
          <el-empty v-if="!submissions.length" description="暂无提交记录" :image-size="60" />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.admin-home {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---------- 欢迎横幅 ---------- */
.banner {
  position: relative;
  height: 140px;
  border-radius: 10px;
  overflow: hidden;
  background: linear-gradient(115deg, #cfe9fd 0%, #e0f1ff 42%, #eef7ff 100%);
  box-shadow: 0 2px 8px rgba(15, 33, 68, 0.04);
}

.banner-inner {
  position: absolute;
  inset: 0;
  z-index: 2;
}

.banner-text {
  position: absolute;
  left: 32px;
  top: 50%;
  transform: translateY(-50%);
}

.banner-text h1 {
  margin: 0 0 8px;
  font-size: 22px;
  font-weight: 700;
  color: #1f2d3d;
  letter-spacing: 1px;
}

.banner-text p {
  margin: 0;
  font-size: 13px;
  color: #6d7d94;
  letter-spacing: 1px;
}

.banner-art {
  position: absolute;
  right: 330px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: center;
  font-family: 'STKaiti', 'KaiTi', 'Songti SC', serif;
  font-style: italic;
  font-size: 19px;
  letter-spacing: 3px;
  color: #1f6ff0;
  line-height: 1.3;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.8);
}

.banner-illus {
  position: absolute;
  right: 0;
  bottom: 0;
  height: 100%;
  width: 52%;
  z-index: 1;
}

.banner-dots {
  position: absolute;
  right: 20px;
  bottom: 12px;
  z-index: 3;
  display: flex;
  gap: 6px;
}

.banner-dots .dot {
  width: 8px;
  height: 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 1px rgba(31, 110, 240, 0.25);
  cursor: pointer;
  transition: all 0.25s;
}

.banner-dots .dot.active {
  width: 20px;
  background: #1878fd;
}

.banner-fade-enter-active,
.banner-fade-leave-active {
  transition: opacity 0.4s;
}

.banner-fade-enter-from,
.banner-fade-leave-to {
  opacity: 0;
}

/* ---------- 统计卡片 ---------- */
.stat-card {
  position: relative;
  background: #fff;
  border-radius: 8px;
  padding: 14px 12px;
  display: flex;
  align-items: flex-start;
  gap: 11px;
  box-shadow: 0 2px 8px rgba(15, 33, 68, 0.04);
  transition: transform 0.2s, box-shadow 0.2s;
  min-height: 100px;
}

.stat-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 18px rgba(15, 33, 68, 0.08);
}

.stat-icon {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-icon svg {
  width: 22px;
  height: 22px;
}

.stat-info {
  flex: 1;
  min-width: 0;
}

.stat-info .value {
  font-size: 24px;
  font-weight: 700;
  color: #1f2d3d;
  line-height: 1.15;
}

.stat-info .label {
  font-size: 12.5px;
  color: #8a97ad;
  margin-top: 2px;
}

.stat-info .trend {
  font-size: 11.5px;
  margin-top: 6px;
}

.ring {
  --pct: 0;
  --ring-color: #f59e0b;
  position: absolute;
  right: 10px;
  top: 12px;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(closest-side, #fff 72%, transparent 73% 100%),
    conic-gradient(var(--ring-color) calc(var(--pct) * 1%), #eef1f6 0);
}

.ring span {
  font-size: 10.5px;
  font-weight: 700;
  color: #1f2d3d;
}

/* ---------- 卡片通用头 ---------- */
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}

.card-title i {
  width: 4px;
  height: 16px;
  border-radius: 2px;
  background: #1878fd;
}

.title-icon {
  width: 17px;
  height: 17px;
  color: #1878fd;
}

.more {
  font-size: 12.5px;
  color: #1878fd;
  cursor: pointer;
  text-decoration: none;
}

.more:hover {
  opacity: 0.8;
}

/* ---------- 图表卡片 ---------- */
.chart-card {
  padding: 12px 16px 4px;
}

.chart-box {
  height: 232px;
}

.batch-select {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: #5e6d82;
  border: 1px solid #dfe6f0;
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
  background: #fff;
}

.batch-select:hover {
  color: #1878fd;
  border-color: #b8d3fd;
}

/* ---------- 列表卡片 ---------- */
.list-card {
  padding: 14px 16px 10px;
  min-height: 250px;
}

.ann-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7.5px 2px;
  border-bottom: 1px dashed #ebeef5;
  cursor: pointer;
}

.ann-item:last-of-type {
  border-bottom: none;
}

.ann-tag {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 4px;
}

.ann-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #3b4a5f;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ann-item:hover .ann-title {
  color: #1878fd;
}

.ann-date {
  flex-shrink: 0;
  font-size: 12px;
  color: #a3adbd;
}

/* 待办 */
.todo-chips {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  margin-bottom: 8px;
}

.chip {
  font-size: 11.5px;
  line-height: 1;
  padding: 5px 9px;
  border-radius: 12px;
  background: #f2f5f9;
  color: #5e6d82;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.chip:hover {
  color: #1878fd;
}

.chip.active {
  background: #1878fd;
  color: #fff;
}

.todo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 2px;
  border-bottom: 1px dashed #ebeef5;
}

.todo-item:last-of-type {
  border-bottom: none;
}

.todo-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.todo-text {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #3b4a5f;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.todo-cat {
  flex-shrink: 0;
  font-size: 12px;
  color: #8a97ad;
}

.todo-date {
  flex-shrink: 0;
  font-size: 12px;
  color: #a3adbd;
}

/* 迷你表格 */
.mini-thead,
.mini-tr {
  display: grid;
  grid-template-columns: 68px 1fr 84px 64px;
  align-items: center;
  gap: 6px;
  padding: 0 6px;
}

.mini-thead {
  height: 30px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 12px;
  color: #5e6d82;
  font-weight: 600;
}

.mini-tr {
  height: 36px;
  border-bottom: 1px solid #f0f3f8;
  font-size: 12.5px;
  color: #3b4a5f;
}

.mini-tr:last-of-type {
  border-bottom: none;
}

.col-time {
  color: #8a97ad;
  font-size: 12px;
}

.col-status {
  text-align: center;
}

.status-pill {
  display: inline-block;
  font-size: 11.5px;
  line-height: 1;
  padding: 4px 9px;
  border-radius: 10px;
}

.banner { height: 150px; background: linear-gradient(90deg, #e3f2ff 0%, #e5f3ffc9 32%, #e9f5ff12 65%), url('/media/login-bg.png') right 83% / 100% auto; }
.banner-illus { display: none; }
.banner-text h1 { font-size: 31px; color: #091c3d; }
.banner-art { right: 35px; color: #113b77; }
.stat-info .label { order: -1; font-size: 13px; color: #637797; }
.stat-info .value { font-size: 27px; }
.stat-card { min-height: 114px; align-items: center; }
.stat-icon { width: 50px; height: 50px; }
.stat-icon svg { width: 28px; height: 28px; }
.chart-box { height: 248px; }
.batch-select { cursor: default; }
@media (max-width: 1450px) {
  .stat-row :deep(.el-col) { max-width: 33.333%; flex: 0 0 33.333%; margin-bottom: 12px; }
  .stat-card:has(.ring) .stat-info { padding-right: 50px; }
}
@media (max-width: 900px) {
  .chart-row :deep(.el-col), .list-row :deep(.el-col) { max-width: 100%; flex: 0 0 100%; margin-bottom: 12px; }
}
@media (max-width: 600px) {
  .stat-row :deep(.el-col) { max-width: 100%; flex: 0 0 100%; }
  .stat-card:has(.ring) .stat-info { padding-right: 0; }
  .ring { top: auto; bottom: 12px; }
  .banner-text { left: 18px; }
  .banner-text h1 { font-size: 23px; }
  .banner-art { display: none; }
  .mini-table { overflow-x: auto; }
  .mini-thead, .mini-tr { min-width: 420px; }
}
</style>
