import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { clearAuthStorage, getAccessToken, isTokenExpired, readStoredUser, roleHome } from '@/utils/token'
import { dropSession, hasRefreshToken, refreshAccessToken } from '@/api/session'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/login/LoginView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    redirect: '/login',
    children: [
      // ===== 学生 =====
      { path: 'student/home', component: () => import('@/views/dashboards/StudentHome.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/my-tasks', component: () => import('@/views/student/MyTasks.vue'), meta: { roles: ['STUDENT'], title: '毕业设计 / 我的任务' } },
      { path: 'student/my-defense', component: () => import('@/views/student/MyDefense.vue'), meta: { roles: ['STUDENT'], title: '毕业设计 / 我的答辩' } },
      { path: 'student/my-archive', component: () => import('@/views/student/MyArchive.vue'), meta: { roles: ['STUDENT'], title: '毕业设计 / 毕业成果' } },
      { path: 'student/topics', component: () => import('@/views/student/TopicCenter.vue'), meta: { roles: ['STUDENT'], title: '学生 / 题目中心' } },
      { path: 'student/selection', component: () => import('@/views/student/MySelection.vue'), meta: { roles: ['STUDENT'], title: '学生 / 我的选题' } },
      { path: 'student/opening', component: () => import('@/views/student/StageReport.vue'), meta: { roles: ['STUDENT'], stage: 'opening', title: '学生 / 开题报告提交' } },
      { path: 'student/midterm', component: () => import('@/views/student/StageReport.vue'), meta: { roles: ['STUDENT'], stage: 'midterm', title: '学生 / 中期答辩材料' } },
      { path: 'student/final', component: () => import('@/views/student/StageReport.vue'), meta: { roles: ['STUDENT'], stage: 'final', title: '学生 / 终期答辩材料' } },
      { path: 'student/material', component: () => import('@/views/student/MaterialView.vue'), meta: { roles: ['STUDENT'], title: '学生 / 毕业材料提交' } },
      { path: 'student/progress', component: () => import('@/views/student/MyProgress.vue'), meta: { roles: ['STUDENT'], title: '学生 / 毕设进度' } },
      { path: 'student/ai-defense', component: () => import('@/views/student/AiDefense.vue'), meta: { roles: ['STUDENT'], title: '学生 / AI模拟答辩' } },
      { path: 'student/ai-chat', component: () => import('@/views/student/AiChat.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/ai-report', component: () => import('@/views/student/AiReport.vue'), meta: { roles: ['STUDENT'], title: 'AI 答辩报告' } },
      { path: 'student/ai-ppt', component: () => import('@/views/student/AiPpt.vue'), meta: { roles: ['STUDENT'], title: '答辩 PPT 大纲' } },
      { path: 'student/ai-knowledge', component: () => import('@/views/student/AiKnowledge.vue'), meta: { roles: ['STUDENT'], title: 'AI 知识准备' } },
      { path: 'teacher/ai-progress', component: () => import('@/views/teacher/AiProgress.vue'), meta: { roles: ['TEACHER'], title: '教师 / AI 进度分析' } },
      // ===== 教师 =====
      { path: 'teacher/home', component: () => import('@/views/dashboards/TeacherHome.vue'), meta: { roles: ['TEACHER'] } },
      { path: 'teacher/topics', component: () => import('@/views/teacher/MyTopics.vue'), meta: { roles: ['TEACHER'], title: '教师 / 我的题目' } },
      { path: 'teacher/selections', component: () => import('@/views/teacher/SelectionReview.vue'), meta: { roles: ['TEACHER'], title: '教师 / 选题审核' } },
      { path: 'teacher/opening', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['TEACHER'], stage: 'opening', title: '教师 / 开题报告审核' } },
      { path: 'teacher/midterm', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['TEACHER'], stage: 'midterm', title: '教师 / 中期答辩审核' } },
      { path: 'teacher/final', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['TEACHER'], stage: 'final', title: '教师 / 终期答辩审核' } },
      { path: 'teacher/materials', component: () => import('@/views/teacher/MaterialReview.vue'), meta: { roles: ['TEACHER'], title: '教师 / 毕业材料审核' } },
      { path: 'teacher/students', component: () => import('@/views/teacher/StudentProgress.vue'), meta: { roles: ['TEACHER'], title: '教师 / 学生毕设进度' } },
      { path: 'teacher/tasks', component: () => import('@/views/teacher/TaskManage.vue'), meta: { roles: ['TEACHER'], title: '毕业设计 / 指导任务' } },
      { path: 'teacher/review-workbench', component: () => import('@/views/teacher/ReviewWorkbench.vue'), meta: { roles: ['TEACHER'], title: '毕业设计 / 评阅与答辩' } },
      // ===== 管理员 =====
      { path: 'admin/home', component: () => import('@/views/dashboards/AdminHome.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/statistics', component: () => import('@/views/admin/AdminStatistics.vue'), meta: { roles: ['ADMIN'], pageBg: '#eff8fe' } },
      { path: 'admin/topics', component: () => import('@/views/admin/AdminTopics.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 题目信息' } },
      { path: 'admin/selections', component: () => import('@/views/teacher/SelectionReview.vue'), meta: { roles: ['ADMIN'], reviewer: 'admin', title: '毕业设计管理 / 选题审核' } },
      { path: 'admin/opening', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['ADMIN'], reviewer: 'admin', stage: 'opening', title: '毕业设计管理 / 开题报告管理' } },
      { path: 'admin/midterm', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['ADMIN'], reviewer: 'admin', stage: 'midterm', title: '毕业设计管理 / 中期答辩管理' } },
      { path: 'admin/final', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['ADMIN'], reviewer: 'admin', stage: 'final', title: '毕业设计管理 / 终期材料管理' } },
      { path: 'admin/materials', component: () => import('@/views/admin/AdminMaterials.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 毕业材料管理' } },
      { path: 'admin/progress', component: () => import('@/views/admin/AdminProgress.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 毕设进度管理' } },
      { path: 'admin/defense-schedule', component: () => import('@/views/admin/DefenseSchedule.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 答辩排期' } },
      { path: 'admin/score-review', component: () => import('@/views/admin/ScoreReview.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 成绩复核' } },
      { path: 'admin/archive', component: () => import('@/views/admin/ArchiveAdmin.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 成果归档' } },
      { path: 'admin/announcements', component: () => import('@/views/admin/AnnouncementAdmin.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 公告管理' } },
      { path: 'admin/users', component: () => import('@/views/admin/UserManagement.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/settings', component: () => import('@/views/admin/SystemSettings.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/academic-schedule', component: () => import('@/views/admin/AcademicSchedule.vue'), meta: { roles: ['ADMIN'], title: '系统设置 / 学年与阶段时间' } },
      { path: 'admin/prompts', component: () => import('@/views/admin/PromptAdmin.vue'), meta: { roles: ['ADMIN'], title: '系统设置 / Prompt 配置' } },
      { path: 'admin/logs', component: () => import('@/views/admin/OperationLogs.vue'), meta: { roles: ['ADMIN'], title: '操作日志' } },
      { path: 'admin/ai-logs', component: () => import('@/views/admin/AiUsageLogs.vue'), meta: { roles: ['ADMIN'], title: '系统设置 / AI 调用日志' } },
      // ===== 通用 =====
      { path: 'profile', component: () => import('@/views/common/ProfileView.vue') },
      { path: 'announcements', component: () => import('@/views/common/AnnouncementList.vue') },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/login' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

interface StoredUser {
  role?: string
  [key: string]: unknown
}

function loginRoute(fullPath: string) {
  const redirect = fullPath && fullPath !== '/' ? { redirect: fullPath } : {}
  return { path: '/login', query: redirect }
}

router.beforeEach(async (to) => {
  const token = getAccessToken()

  // 登录页等公开路由: 已登录且 token 未过期则不重复展示登录页
  if (to.meta.public) {
    const stored = readStoredUser<StoredUser>()
    if (token && stored?.role && !isTokenExpired(token)) return { path: roleHome(stored.role) }
    return true
  }

  // ===== 1) exp 校验(不验签，只判过期) + 静默续期 =====
  if (!token || isTokenExpired(token)) {
    if (!hasRefreshToken()) {
      clearAuthStorage()
      return loginRoute(to.fullPath)
    }
    try {
      // 与 api/request.ts 共用同一个 refresh 单例，不会重复刷新
      await refreshAccessToken()
    } catch {
      dropSession()
      return loginRoute(to.fullPath)
    }
    if (isTokenExpired(getAccessToken())) {
      dropSession()
      return loginRoute(to.fullPath)
    }
  }

  // ===== 2) 用户信息缺失(本地存储被清) → 视为未登录 =====
  const user = readStoredUser<StoredUser>()
  if (!user?.role) {
    clearAuthStorage()
    return loginRoute(to.fullPath)
  }

  // ===== 3) 角色越权: 重定向到自己首页，而不是白屏 =====
  const roles = (to.meta.roles as string[] | undefined) || []
  if (roles.length > 0 && !roles.includes(user.role)) {
    ElMessage.error('无权限访问该页面')
    return { path: roleHome(user.role) }
  }

  if (to.path === '/') return { path: roleHome(user.role) }
  return true
})

export default router
