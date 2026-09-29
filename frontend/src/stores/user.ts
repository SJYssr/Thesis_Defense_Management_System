import { defineStore } from 'pinia'
import { authApi } from '@/api/auth'

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
  },
  actions: {
    setSession(data: { access_token: string; refresh_token?: string; user: UserInfo }) {
      localStorage.setItem('access_token', data.access_token)
      if (data.refresh_token) {
        localStorage.setItem('refresh_token', data.refresh_token)
      }
      this.user = data.user
      localStorage.setItem('user_info', JSON.stringify(data.user))
    },
    async refreshProfile() {
      const res = await authApi.getProfile()
      this.user = res.data
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
