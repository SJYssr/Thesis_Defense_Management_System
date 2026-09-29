import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/login/LoginView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    redirect: '/login',
    children: [
      // ===== 学生 =====
      { path: 'student/home', component: () => import('@/views/dashboards/StudentHome.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/topics', component: () => import('@/views/student/TopicCenter.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/selection', component: () => import('@/views/student/MySelection.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/opening', component: () => import('@/views/student/StageReport.vue'), meta: { roles: ['STUDENT'], stage: 'opening' } },
      { path: 'student/midterm', component: () => import('@/views/student/StageReport.vue'), meta: { roles: ['STUDENT'], stage: 'midterm' } },
      { path: 'student/final', component: () => import('@/views/student/StageReport.vue'), meta: { roles: ['STUDENT'], stage: 'final' } },
      { path: 'student/material', component: () => import('@/views/student/MaterialView.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/progress', component: () => import('@/views/student/MyProgress.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/ai-defense', component: () => import('@/views/student/AiDefense.vue'), meta: { roles: ['STUDENT'] } },
      { path: 'student/ai-chat', component: () => import('@/views/student/AiChat.vue'), meta: { roles: ['STUDENT'] } },
      // ===== 教师 =====
      { path: 'teacher/home', component: () => import('@/views/dashboards/TeacherHome.vue'), meta: { roles: ['TEACHER'] } },
      { path: 'teacher/topics', component: () => import('@/views/teacher/MyTopics.vue'), meta: { roles: ['TEACHER'] } },
      { path: 'teacher/selections', component: () => import('@/views/teacher/SelectionReview.vue'), meta: { roles: ['TEACHER'] } },
      { path: 'teacher/opening', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['TEACHER'], stage: 'opening' } },
      { path: 'teacher/midterm', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['TEACHER'], stage: 'midterm' } },
      { path: 'teacher/final', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['TEACHER'], stage: 'final' } },
      { path: 'teacher/materials', component: () => import('@/views/teacher/MaterialReview.vue'), meta: { roles: ['TEACHER'] } },
      { path: 'teacher/students', component: () => import('@/views/teacher/StudentProgress.vue'), meta: { roles: ['TEACHER'] } },
      // ===== 管理员 =====
      { path: 'admin/home', component: () => import('@/views/dashboards/AdminHome.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/statistics', component: () => import('@/views/admin/AdminStatistics.vue'), meta: { roles: ['ADMIN'], pageBg: '#eff8fe' } },
      { path: 'admin/topics', component: () => import('@/views/admin/AdminTopics.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/selections', component: () => import('@/views/admin/AdminSelections.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/opening', component: () => import('@/views/admin/AdminOpening.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 开题报告管理' } },
      { path: 'admin/midterm', component: () => import('@/views/admin/AdminMidterm.vue'), meta: { roles: ['ADMIN'], title: '毕业设计管理 / 中期答辩管理' } },
      { path: 'admin/final', component: () => import('@/views/teacher/StageReview.vue'), meta: { roles: ['ADMIN'], stage: 'final', reviewer: 'admin' } },
      { path: 'admin/materials', component: () => import('@/views/teacher/MaterialReview.vue'), meta: { roles: ['ADMIN'], reviewer: 'admin' } },
      { path: 'admin/progress', component: () => import('@/views/admin/AdminProgress.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/announcements', component: () => import('@/views/admin/AnnouncementAdmin.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/users', component: () => import('@/views/admin/UserManagement.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/settings', component: () => import('@/views/admin/SystemSettings.vue'), meta: { roles: ['ADMIN'] } },
      { path: 'admin/logs', component: () => import('@/views/admin/OperationLogs.vue'), meta: { roles: ['ADMIN'] } },
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

router.beforeEach((to) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.public) {
    return true
  }
  if (!token) {
    return { path: '/login' }
  }
  const user = JSON.parse(localStorage.getItem('user_info') || 'null')
  const roles = (to.meta.roles as string[]) || []
  if (roles.length && user && !roles.includes(user.role)) {
    ElMessage.error('没有权限访问该页面')
    const map: Record<string, string> = { ADMIN: '/admin/home', TEACHER: '/teacher/home', STUDENT: '/student/home' }
    return { path: map[user.role] || '/login' }
  }
  if (to.path === '/' || to.path === '/login') {
    if (user) {
      const map: Record<string, string> = { ADMIN: '/admin/home', TEACHER: '/teacher/home', STUDENT: '/student/home' }
      return { path: map[user.role] || '/login' }
    }
  }
  return true
})

export default router
