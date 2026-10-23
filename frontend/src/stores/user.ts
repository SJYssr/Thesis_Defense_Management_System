import { defineStore } from 'pinia'
import { authApi } from '@/api/auth'
import { registerProfileSync, resetSessionExpiredFlag } from '@/api/session'

interface UserInfo {
  id: number
  username: string
  real_name: string
  role: string
  avatar?: string
  profile_id?: number
  student_no?: string
  teacher_no?: string
  title?: string
  department?: string
  grade?: string
  /** P1-B: 登录/刷新/ /auth/me 都带该字段，true 表示仍在用初始密码 */
  must_change_password?: boolean
  [key: string]: unknown
}

export const useUserStore = defineStore('user', {
  state: () => ({
    user: JSON.parse(localStorage.getItem('user_info') || 'null') as UserInfo | null,
  }),
  getters: {
    isLoggedIn: (state) => !!state.user,
    role: (state) => state.user?.role || '',
    rolePrefix: (state) => {
      const map: Record<string, string> = { ADMIN: '/admin', TEACHER: '/teacher', STUDENT: '/student' }
      return map[state.user?.role || ''] || '/login'
    },
    /** 是否仍在使用初始密码(只做提示与引导，不做强制拦截) */
    mustChangePassword: (state) => state.user?.must_change_password === true,
  },
  actions: {
    /**
     * 把 store 注册给 api/session.ts: 401 恢复时 refresh 成功后，
     * session 会重新拉 /auth/profile 并通过该回调把最新 user 同步进内存态。
     */
    bindProfileSync() {
      registerProfileSync((user) => {
        this.user = user as UserInfo
      })
    },
    setSession(data: { access_token: string; refresh_token?: string; user: UserInfo }) {
      resetSessionExpiredFlag()
      this.bindProfileSync()
      localStorage.setItem('access_token', data.access_token)
      if (data.refresh_token) {
        localStorage.setItem('refresh_token', data.refresh_token)
      }
      this.user = data.user
      localStorage.setItem('user_info', JSON.stringify(data.user))
    },
    async refreshProfile() {
      const res = await authApi.getProfile()
      this.user = res.data as UserInfo
      localStorage.setItem('user_info', JSON.stringify(res.data))
    },
    logout() {
      authApi.logout().catch(() => undefined)
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user_info')
      this.user = null
    },
  },
})
