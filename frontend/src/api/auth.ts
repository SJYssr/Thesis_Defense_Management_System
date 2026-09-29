import request from './request'

export interface LoginPayload {
  username: string
  password: string
}

export const authApi = {
  login: (data: LoginPayload) => request.post('/auth/login', data),
  logout: () => request.post('/auth/logout'),
  refresh: (refresh_token: string) => request.post('/auth/refresh', { refresh_token }),
  getProfile: () => request.get('/auth/profile'),
  updateProfile: (data: Record<string, unknown>) => request.put('/auth/profile', data),
  changePassword: (data: { old_password: string; new_password: string }) => request.put('/auth/password', data),
}
