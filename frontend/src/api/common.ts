import request from './request'

export const aiApi = {
  listDocuments: () => request.get('/ai/documents'),
  ragConfig: () => request.get('/ai/rag/config'),
  indexDocument: (id: number) => request.post(`/ai/documents/${id}/index`),
  documentStatus: (id: number) => request.get(`/ai/documents/${id}/status`),
  indexReports: () => request.post('/ai/documents/index-reports'),
  ragQuery: (data: { query: string; top_k?: number }) => request.post('/ai/rag/query', data),
  generateQuestions: () => request.post('/ai/defense/questions/generate'),
  createSession: (questionCount: number) => request.post(`/ai/defense/sessions?question_count=${questionCount}`),
  startSession: (id: number) => request.post(`/ai/defense/sessions/${id}/start`),
  answer: (id: number, data: { question: string; answer: string }) =>
    request.post(`/ai/defense/sessions/${id}/answer`, data),
  next: (id: number) => request.post(`/ai/defense/sessions/${id}/next`),
  sessions: () => request.get('/ai/defense/sessions'),
  sessionDetail: (id: number) => request.get(`/ai/defense/sessions/${id}`),
  report: (id: number) => request.get(`/ai/defense/sessions/${id}/report`),
  pptOutline: () => request.post('/ai/ppt-outline/generate'),
  analyseProgress: (studentId: number) => request.post(`/ai/progress/student/${studentId}/analyse`),
}

export const dashboardApi = {
  home: () => request.get('/dashboard/home'),
  stats: () => request.get('/dashboard/stats'),
  analytics: () => request.get('/dashboard/analytics'),
  adminActivity: () => request.get('/dashboard/admin-activity'),
  teacherActivity: () => request.get('/dashboard/teacher-activity'),
}

export const announcementApi = {
  list: (params: Record<string, unknown>) => request.get('/announcements', { params }),
  detail: (id: number) => request.get(`/announcements/${id}`),
  adminList: (params: Record<string, unknown>) => request.get('/admin/announcements', { params }),
  create: (data: Record<string, unknown>) => request.post('/admin/announcements', data),
  update: (id: number, data: Record<string, unknown>) => request.put(`/admin/announcements/${id}`, data),
  remove: (id: number) => request.delete(`/admin/announcements/${id}`),
}

export const notificationApi = {
  list: (params: Record<string, unknown>) => request.get('/notifications', { params }),
  markRead: (id: number) => request.post(`/notifications/${id}/read`),
  markAllRead: () => request.post('/notifications/read-all'),
}

export const userAdminApi = {
  list: (role: string, params: Record<string, unknown>) => request.get(`/admin/users/${role}`, { params }),
  create: (role: string, data: Record<string, unknown>) => request.post(`/admin/users/${role}`, data),
  update: (role: string, id: number, data: Record<string, unknown>) => request.put(`/admin/users/${role}/${id}`, data),
  remove: (role: string, id: number) => request.delete(`/admin/users/${role}/${id}`),
  colleges: () => request.get('/org/colleges'),
  majors: (collegeId?: number) => request.get('/org/majors', { params: { college_id: collegeId } }),
}

export const operationLogApi = {
  list: (params: Record<string, unknown>) => request.get('/admin/operation-logs', { params }),
}

export const uploadApi = {
  file: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request.post('/files/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}
