<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { loadPublicConfig, systemName } from '@/stores/app'
import { notificationApi } from '@/api/common'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const isStudent = computed(() => userStore.role === 'STUDENT')
const sidebarWidth = computed(() => isStudent.value ? '220px' : '264px')
const ROLE_CN: Record<string, string> = { ADMIN: '管理员', TEACHER: '教师', STUDENT: '学生' }

/* ---------- 侧栏填充风格图标(与设计图一致的实底图标) ---------- */
const ICONS: Record<string, string> = {
  home: '<path d="M11.4 2.9a.9.9 0 0 1 1.2 0l9 7.6c.4.4.5 1 .1 1.4-.3.4-.9.4-1.3.1l-.7-.6V20a1 1 0 0 1-1 1h-4.6a1 1 0 0 1-1-1v-5.1h-2.2V20a1 1 0 0 1-1 1H5.3a1 1 0 0 1-1-1v-8.6l-.7.6c-.4.3-1 .3-1.3-.1-.4-.4-.3-1 .1-1.4l9-7.6z"/>',
  chart: '<path d="M3.5 20.2a1 1 0 0 1 1-1h15a1 1 0 1 1 0 2h-15a1 1 0 0 1-1-1zM5.2 10.4h2.6a1 1 0 0 1 1 1v6H4.2v-6a1 1 0 0 1 1-1zM10.7 3.7h2.6a1 1 0 0 1 1 1v12.7H9.7V4.7a1 1 0 0 1 1-1zM16.2 7.4h2.6a1 1 0 0 1 1 1v9H15.2v-9a1 1 0 0 1 1-1z"/>',
  cap: '<path d="M12 2.7 1.6 7.9a.9.9 0 0 0 0 1.6l2 1v4.3a1 1 0 1 0 2 0v-3.3l1.6.8v3.9c0 1.7 2.6 3.1 4.8 3.1s4.8-1.4 4.8-3.1v-3.9l4.6-2.3a.9.9 0 0 0 0-1.6L12 2.7z"/><path d="M18.3 12.2a.85.85 0 0 1 .85.85v2.6a.85.85 0 0 1-1.7 0V13a.85.85 0 0 1 .85-.8z"/>',
  horn: '<path d="M20.5 4.6c0-.9-1-1.5-1.8-1L8.9 8.2H5.2A2.7 2.7 0 0 0 2.5 11v2.3a2.7 2.7 0 0 0 2.7 2.7h.9l1.1 4.4a1 1 0 0 0 1 .7h1a1 1 0 0 0 .96-1.24L9.4 16l9.3 4.7c.8.4 1.8-.1 1.8-1V4.6z"/>',
  userbox: '<path fill-rule="evenodd" d="M5.5 2.5h13a3 3 0 0 1 3 3v13a3 3 0 0 1-3 3h-13a3 3 0 0 1-3-3v-13a3 3 0 0 1 3-3zm6.5 6.2a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2zm-5.2 7.5h10.4a.9.9 0 0 0 .9-1c-.4-2.8-3.1-4.6-6.1-4.6s-5.7 1.8-6.1 4.6a.9.9 0 0 0 .9 1z"/>',
  gear: '<path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.49 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.65 8.87c-.12.22-.07.47.12.61l2.03 1.58c-.05.3-.07.63-.07.94s.02.64.07.94l-2.03 1.58a.5.5 0 0 0-.12.62l1.9 3.31c.12.22.38.31.6.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.04.24.23.42.47.42h3.84c.24 0 .44-.18.47-.42l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.09.48 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.62l-2.06-1.56zM12 15.68a3.68 3.68 0 1 1 0-7.36 3.68 3.68 0 0 1 0 7.36z"/>',
  doc: '<path d="M6.5 2h7.1l5.9 5.9V20a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm6.6 1.8V9h5.2L13.1 3.8zM7.8 12.2h8.4a1 1 0 1 1 0 2H7.8a1 1 0 1 1 0-2zm0 4h8.4a1 1 0 1 1 0 2H7.8a1 1 0 1 1 0-2z"/>',
  spark: '<path d="M12 2.5l1.9 5.2 5.2 1.9-5.2 1.9L12 16.7l-1.9-5.2-5.2-1.9 5.2-1.9L12 2.5zM18.5 15l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9.9-2.4z"/>',
}

/* ---------- 菜单数据: 管理员结构与设计图严格一致 ---------- */
interface MenuLeaf {
  title: string
  path: string
  query?: Record<string, string>
}
interface MenuNode {
  title: string
  icon: string
  path?: string
  query?: Record<string, string>
  chevron?: boolean
  children?: MenuLeaf[]
}

const menus = computed<MenuNode[]>(() => {
  if (userStore.role === 'ADMIN') {
    return [
      { title: '系统首页', icon: 'home', path: '/admin/home' },
      { title: '数据统计', icon: 'chart', path: '/admin/statistics', chevron: true },
      {
        title: '毕业设计管理',
        icon: 'cap',
        children: [
          { title: '题目信息', path: '/admin/topics' },
          { title: '选题审核', path: '/admin/selections' },
          { title: '开题报告', path: '/admin/opening' },
          { title: '中期答辩', path: '/admin/midterm' },
          { title: '终期材料', path: '/admin/final' },
          { title: '毕业材料', path: '/admin/materials' },
          { title: '毕设进度', path: '/admin/progress' },
          { title: '成果归档', path: '/admin/archive' },
        ],
      },
      {
        title: '答辩与成绩',
        icon: 'chart',
        children: [
          { title: '答辩排期', path: '/admin/defense-schedule' },
          { title: '成绩复核', path: '/admin/score-review' },
        ],
      },
      { title: '公告管理', icon: 'horn', path: '/admin/announcements', chevron: true },
      { title: '用户管理', icon: 'userbox', children: [
        { title: '管理员信息', path: '/admin/users', query: { role: 'ADMIN' } },
        { title: '教师信息', path: '/admin/users', query: { role: 'TEACHER' } },
        { title: '学生信息', path: '/admin/users', query: { role: 'STUDENT' } },
      ] },
      {
        title: '个人中心',
        icon: 'gear',
        children: [
          { title: '个人信息', path: '/profile', query: { tab: 'info' } },
          { title: '修改密码', path: '/profile', query: { tab: 'password' } },
        ],
      },
      { title: '系统设置', icon: 'gear', path: '/admin/settings', chevron: true },
      { title: '学年配置', icon: 'gear', path: '/admin/academic-schedule', chevron: true },
      { title: 'Prompt 配置', icon: 'doc', path: '/admin/prompts', chevron: true },
      { title: '操作日志', icon: 'doc', path: '/admin/logs' },
      { title: 'AI 调用日志', icon: 'chart', path: '/admin/ai-logs', chevron: true },
    ]
  }
  if (userStore.role === 'TEACHER') {
    /* 与设计稿 15 的侧栏严格一致（平铺菜单） */
    return [
      { title: '系统首页', icon: 'home', path: '/teacher/home' },
      { title: '我的题目', icon: 'doc', path: '/teacher/topics' },
      { title: '选题审核', icon: 'userbox', path: '/teacher/selections' },
      { title: '开题报告', icon: 'doc', path: '/teacher/opening' },
      { title: '中期答辩', icon: 'doc', path: '/teacher/midterm' },
      { title: '终期材料', icon: 'doc', path: '/teacher/final' },
      { title: '毕业材料', icon: 'doc', path: '/teacher/materials' },
      { title: '学生毕设进度', icon: 'userbox', path: '/teacher/students' },
      { title: '指导任务', icon: 'doc', path: '/teacher/tasks' },
      { title: '评阅与答辩', icon: 'userbox', path: '/teacher/review-workbench' },
      { title: 'AI 进度分析', icon: 'spark', path: '/teacher/ai-progress' },
      { title: '公告信息', icon: 'horn', path: '/announcements' },
      { title: '个人中心', icon: 'userbox', path: '/profile' },
    ]
  }
  return [
    { title: '系统首页', icon: 'home', path: '/student/home' },
    { title: '我的任务', icon: 'doc', path: '/student/my-tasks' },
    { title: '我的答辩', icon: 'userbox', path: '/student/my-defense' },
    { title: '毕业成果', icon: 'cap', path: '/student/my-archive' },
    { title: '题目中心', icon: 'doc', path: '/student/topics' },
    { title: '我的选题', icon: 'doc', path: '/student/selection' },
    { title: '开题报告', icon: 'doc', path: '/student/opening' },
    { title: '中期答辩', icon: 'doc', path: '/student/midterm' },
    { title: '终期材料', icon: 'doc', path: '/student/final' },
    { title: '毕业材料', icon: 'doc', path: '/student/material' },
    { title: '毕设进度', icon: 'chart', path: '/student/progress' },
    { title: 'AI 模拟答辩(练习)', icon: 'spark', path: '/student/ai-defense' },
    { title: '答辩报告', icon: 'doc', path: '/student/ai-report' },
    { title: 'PPT 大纲', icon: 'doc', path: '/student/ai-ppt' },
    { title: 'AI 知识准备', icon: 'spark', path: '/student/ai-knowledge' },
    { title: '知识问答', icon: 'spark', path: '/student/ai-chat' },
  ]
})

/* ---------- 展开/折叠 ---------- */
const collapsed = ref(false)
const isNarrow = ref(false)
const expanded = ref<Set<string>>(new Set())
function syncViewport() {
  const narrow = window.matchMedia('(max-width: 900px)').matches
  if (narrow !== isNarrow.value) collapsed.value = narrow
  isNarrow.value = narrow
}

function isLeafActive(leaf: MenuLeaf) {
  if (route.path !== leaf.path) return false
  if (leaf.query?.role) return route.query.role === leaf.query.role
  if (leaf.query?.tab) return (route.query.tab || 'password') === leaf.query.tab
  return !route.query.role && !route.query.tab
}

function containingGroup(node: MenuNode) {
  return node.children?.some(isLeafActive)
}

/* 顶层直链项需同时匹配 path 与 query（管理员/教师/学生信息共用一个路由） */
function isExactActive(node: MenuNode) {
  if (route.path !== node.path) return false
  const want = node.query?.role
  return want ? route.query.role === want : !route.query.role
}

// 首次加载展开所有分组(与设计图一致); 之后仅保证包含当前页面的分组展开
let menusInitialized = false
watch(
  () => route.path,
  () => {
    if (!menusInitialized) {
      menusInitialized = true
      for (const node of menus.value) {
        if (node.children && node.title !== '个人中心') expanded.value.add(node.title)
      }
    }
    for (const node of menus.value) {
      if (node.children && containingGroup(node)) expanded.value.add(node.title)
    }
  },
  { immediate: true },
)

function toggleGroup(node: MenuNode) {
  if (collapsed.value) return
  const next = new Set(expanded.value)
  next.has(node.title) ? next.delete(node.title) : next.add(node.title)
  expanded.value = next
}

function go(path: string, query?: Record<string, string>) {
  if (isNarrow.value) collapsed.value = true
  router.push({ path, query })
}

/* ---------- 顶栏: 页面标题 ---------- */
const pageTitle = computed(() => {
  if (route.path === '/admin/users') {
    const r = String(route.query.role || '').toUpperCase()
    const n = r === 'ADMIN' ? '管理员信息管理' : r === 'TEACHER' ? '教师信息管理' : '学生信息管理'
    return `毕业设计管理 / ${n}`
  }
  if (route.path === '/profile') {
    return route.query.tab === 'info' ? '个人中心 / 个人信息' : '个人中心 / 修改密码'
  }
  if (route.meta.title) return String(route.meta.title)
  for (const node of menus.value) {
    if (node.path === route.path) return node.title
    if (node.children) {
      const leaf = node.children.find(isLeafActive)
      if (leaf) return leaf.title
    }
  }
  if (route.path === '/profile') return '个人中心'
  if (route.path === '/announcements') return '系统公告'
  return systemName.value
})

/* ---------- 全屏 ---------- */
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen?.()
  } else {
    document.exitFullscreen?.()
  }
}

/* ---------- 通知中心 ---------- */
const notifVisible = ref(false)
const notifications = ref<any[]>([])
const unreadCount = ref(0)
let timer: number | undefined

async function loadNotifications() {
  try {
    const res = await notificationApi.list({ page: 1, page_size: 10 })
    notifications.value = res.data.items
    unreadCount.value = res.data.unread || 0
  } catch {
    /* 忽略 */
  }
}

async function markRead(id: number) {
  await notificationApi.markRead(id)
  await loadNotifications()
}

async function markAllRead() {
  await notificationApi.markAllRead()
  await loadNotifications()
}

function handleCommand(cmd: string) {
  if (cmd === 'profile') {
    router.push('/profile')
  } else if (cmd === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}

onMounted(() => {
  syncViewport()
  window.addEventListener('resize', syncViewport)
  loadPublicConfig()
  loadNotifications()
  timer = window.setInterval(loadNotifications, 30000)
})
onUnmounted(() => {
  window.removeEventListener('resize', syncViewport)
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <el-container class="app-shell" :class="{ 'student-shell': isStudent, 'teacher-shell': userStore.role === 'TEACHER', 'admin-shell': userStore.role === 'ADMIN' }">
    <div v-if="isNarrow && !collapsed" class="sidebar-backdrop" @click="collapsed = true" />
    <!-- 深蓝侧栏 -->
    <el-aside :width="isNarrow ? (collapsed ? '0px' : sidebarWidth) : (collapsed ? '64px' : sidebarWidth)" class="sidebar" :class="{ 'sidebar-mobile': isNarrow, 'sidebar-mobile-closed': isNarrow && collapsed }">
      <div class="logo" @click="collapsed = !collapsed">
        <svg class="logo-cap" viewBox="0 0 24 24" fill="none">
          <path d="M12 3 2 8l10 5 10-5-10-5z" fill="#fff" />
          <path d="M6.5 10.8v3.4c0 1.4 2.5 2.8 5.5 2.8s5.5-1.4 5.5-2.8v-3.4L12 13.6l-5.5-2.8z" fill="#fff" />
          <path d="M20.4 8.6v5.2" stroke="#fff" stroke-width="1.6" stroke-linecap="round" />
          <circle cx="20.4" cy="14.6" r="1.1" fill="#fff" />
        </svg>
        <span v-if="!collapsed" class="logo-text">{{ systemName }}</span>
      </div>
      <el-scrollbar>
        <div class="menu-wrap" :class="{ collapsed }">
          <template v-for="node in menus" :key="node.title">
            <!-- 一级: 直接链接 -->
            <div
              v-if="!node.children"
              class="menu-item"
              :class="{ active: isExactActive(node) }"
              @click="go(node.path!, node.query)"
            >
              <svg class="m-icon" viewBox="0 0 24 24" fill="currentColor" v-html="ICONS[node.icon]" />
              <span v-if="!collapsed" class="m-label">{{ node.title }}</span>
              <svg v-if="node.chevron && !collapsed" class="m-arrow" viewBox="0 0 12 12">
                <path d="M2.5 4.2 6 7.7l3.5-3.5" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </div>
            <!-- 一级: 可展开分组 -->
            <template v-else>
              <div
                class="menu-item group-header"
                :class="{ 'has-active': containingGroup(node) && !collapsed }"
                @click="toggleGroup(node)"
              >
                <svg class="m-icon" viewBox="0 0 24 24" fill="currentColor" v-html="ICONS[node.icon]" />
                <span v-if="!collapsed" class="m-label">{{ node.title }}</span>
                <svg
                  v-if="!collapsed"
                  class="m-arrow"
                  :class="{ open: expanded.has(node.title) }"
                  viewBox="0 0 12 12"
                >
                  <path d="M2.5 4.2 6 7.7l3.5-3.5" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </div>
              <div v-if="expanded.has(node.title) && !collapsed" class="sub-menu">
                <div
                  v-for="leaf in node.children"
                  :key="leaf.title"
                  class="menu-sub-item"
                  :class="{ active: isLeafActive(leaf) }"
                  @click="go(leaf.path, leaf.query)"
                >
                  <svg class="sub-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h6"/></svg><span class="sub-title">{{ leaf.title }}</span>
                </div>
              </div>
            </template>
          </template>
        </div>
      </el-scrollbar>
    </el-aside>

    <el-container>
      <!-- 顶部栏 -->
      <el-header class="topbar">
        <div class="topbar-left">
          <el-icon :size="18" class="tb-btn" @click="collapsed = !collapsed">
            <Expand v-if="collapsed" />
            <Fold v-else />
          </el-icon>
          <el-icon :size="17" class="home-icon"><HomeFilled /></el-icon>
          <span class="page-title">{{ pageTitle }}</span>
        </div>

        <div class="topbar-search">
          <el-input placeholder="请输入关键词搜索..." :prefix-icon="'Search'" />
        </div>

        <div class="topbar-right">
          <el-popover v-model:visible="notifVisible" placement="bottom" :width="380">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99" class="tb-btn">
                <el-icon :size="18"><Bell /></el-icon>
              </el-badge>
            </template>
            <div class="notif-header">
              <span>通知中心</span>
              <el-button link type="primary" size="small" @click="markAllRead">全部已读</el-button>
            </div>
            <el-empty v-if="!notifications.length" description="暂无通知" :image-size="60" />
            <div v-else class="notif-list">
              <div v-for="n in notifications" :key="n.id" class="notif-item" :class="{ unread: !n.is_read }" @click="markRead(n.id)">
                <div class="notif-title">
                  <el-tag size="small" :type="n.type === 'REVIEW_RESULT' ? 'success' : n.type === 'REMINDER' ? 'warning' : 'info'">
                    {{ n.type === 'REVIEW_RESULT' ? '审核' : n.type === 'REMINDER' ? '提醒' : '通知' }}
                  </el-tag>
                  {{ n.title }}
                </div>
                <div class="notif-content">{{ n.content }}</div>
                <div class="notif-time">{{ (n.created_at || '').replace('T', ' ').slice(0, 16) }}</div>
              </div>
            </div>
          </el-popover>

          <el-icon :size="17" class="tb-btn" @click="toggleFullscreen"><FullScreen /></el-icon>

          <el-dropdown @command="handleCommand">
            <span class="user-chip">
              <span class="user-avatar">
                <svg viewBox="0 0 24 24" fill="#fff">
                  <circle cx="12" cy="8.2" r="3.6" />
                  <path d="M4.8 20.4c.6-3.6 3.6-5.6 7.2-5.6s6.6 2 7.2 5.6c.1.6-.4 1.2-1 1.2H5.8c-.6 0-1.1-.6-1-1.2z" />
                </svg>
              </span>
              <span class="user-name">{{ userStore.role === 'ADMIN' ? '管理员' : (userStore.user?.real_name || ROLE_CN[userStore.role]) }}</span>
              <el-icon :size="12" color="#909399"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人中心</el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main-area" :style="route.meta.pageBg ? { background: String(route.meta.pageBg) } : undefined">
        <router-view :key="route.path" />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell { height: 100dvh; min-width: 0; }
.app-shell > .el-container { min-width: 0; }
.sidebar-backdrop { position: fixed; inset: 0; z-index: 20; background: rgba(15, 33, 68, .42); }
.sidebar {
  background: #102d4d;
  display: flex;
  flex-direction: column;
  transition: width 0.25s;
  overflow: hidden;
  flex-shrink: 0;
}
.sidebar-mobile { position: fixed; top: 0; bottom: 0; left: 0; z-index: 21; }
.sidebar-mobile-closed { visibility: hidden; pointer-events: none; }

.logo {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
  cursor: pointer;
}

.logo-cap {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
}

.logo-text {
  color: #fff;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0;
  white-space: nowrap;
}

.menu-wrap {
  padding: 14px 15px 24px;
}

.menu-wrap.collapsed {
  padding: 10px 8px 24px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 44px;
  padding: 0 12px;
  border-radius: 5px;
  color: #e2eaf7;
  cursor: pointer;
  font-size: 14px;
  margin-bottom: 4px;
  transition: all 0.2s;
}

.menu-wrap.collapsed .menu-item {
  justify-content: center;
  padding: 0;
}

.menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}

.menu-item.active {
  background: #1878fd;
  color: #fff;
  font-weight: 500;
  box-shadow: 0 2px 8px rgba(24, 120, 253, 0.4);
}

.m-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.m-label {
  flex: 1;
  white-space: nowrap;
}

.m-arrow {
  width: 12px;
  height: 12px;
  color: #7c8aa5;
  transform: rotate(0deg);
  transition: transform 0.2s;
}

.m-arrow.open {
  transform: rotate(180deg);
}

.menu-item.active .m-arrow {
  color: rgba(255, 255, 255, 0.85);
}

.sub-menu {
  margin-bottom: 4px;
}

.menu-sub-item {
  height: 42px;
  display: flex;
  align-items: center;
  padding: 0 12px 0 26px;
  border-radius: 8px;
  color: #e2eaf7;
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.2s;
}

.menu-sub-item::before {
  content: '';
  width: 12px;
  height: 15px;
  border: 1.5px solid currentColor;
  border-radius: 2px;
  background: transparent;
  margin-right: 10px;
  transition: background 0.2s;
}

.menu-sub-item:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.06);
}

.menu-sub-item:hover::before {
  background: #aab2c2;
}

.menu-sub-item.active {
  background: #1878fd;
  color: #fff;
}

.menu-sub-item.active::before {
  background: transparent;
}

/* ---------- 顶栏 ---------- */
.topbar {
  height: 64px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e8ecf3;
  gap: 16px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1 1 0;
}

.tb-btn {
  cursor: pointer;
  color: #5e6d82;
}

.tb-btn:hover {
  color: #1878fd;
}

.home-icon {
  color: #1878fd;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f2d3d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-search {
  flex: 1 1 220px;
  min-width: 0;
  display: flex;
  justify-content: center;
}

.topbar-search :deep(.el-input) {
  width: 360px;
}

.topbar-search :deep(.el-input__wrapper) {
  border-radius: 6px;
  background: #eef7ff;
  box-shadow: none;
  height: 34px;
}

.topbar-search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #1878fd inset;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 22px;
  flex-shrink: 0;
  justify-content: flex-end;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
}

.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #1878fd;
  display: flex;
  align-items: center;
  justify-content: center;
}

.user-avatar svg {
  width: 20px;
  height: 20px;
}

.user-name {
  font-size: 14px;
  color: #1f2d3d;
}

.main-area {
  padding: 14px 18px;
  overflow-y: auto;
  min-width: 0;
}
@media (max-width: 1100px) {
  .topbar-search { display: none; }
  .topbar-right { gap: 14px; }
}
@media (max-width: 600px) {
  .topbar { padding: 0 14px; gap: 10px; }
  .topbar-left { gap: 8px; }
  .home-icon, .user-name { display: none; }
  .topbar-right { gap: 12px; }
  .main-area { padding: 12px; }
}

.notif-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  margin-bottom: 8px;
}

.notif-list {
  max-height: 360px;
  overflow-y: auto;
}

.notif-item {
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
}

.notif-item:hover {
  background: #eef7ff;
}

.notif-item.unread .notif-title {
  font-weight: 600;
}

.notif-title {
  font-size: 13px;
  margin-bottom: 4px;
}

.notif-content {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notif-time {
  font-size: 11px;
  color: #c0c4cc;
  margin-top: 2px;
}

.student-shell .sidebar { background: #112b50; }
.student-shell .logo { padding: 0 18px; height: 60px; gap: 8px; }
.student-shell .logo-text { font-size: 15px; letter-spacing: 0; }
.student-shell .logo-cap { width: 25px; }
.student-shell .menu-wrap { padding: 14px 12px; }
.student-shell .menu-item { height: 42px; font-size: 13px; margin-bottom: 6px; gap: 10px; }
.student-shell .m-icon { width: 16px; height: 16px; }
.student-shell .topbar { height: 60px; }
.student-shell .home-icon { display: none; }
.student-shell .main-area { background: #f5f6fa; padding: 26px; }
.student-shell .topbar-search { justify-content: flex-end; }
.student-shell .topbar-search :deep(.el-input) { width: 260px; }
.student-shell .topbar-search :deep(.el-input__wrapper) { background: #f8f9fc; border-radius: 18px; }
@media (max-width: 700px) { .student-shell .main-area { padding: 16px; } }
.menu-sub-item::before { display: none; }
.sub-icon { width: 20px; height: 20px; margin-right: 14px; flex-shrink: 0; }
.menu-sub-item { border-radius: 5px; }
</style>
