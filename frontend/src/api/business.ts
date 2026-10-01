import request from './request'

// ---------- 题目 ----------
export const topicApi = {
  list: (params: Record<string, unknown>) => request.get('/topics', { params }),
  detail: (id: number) => request.get(`/topics/${id}`),
  teacherList: (params: Record<string, unknown>) => request.get('/teacher/topics', { params }),
  create: (data: Record<string, unknown>) => request.post('/teacher/topics', data),
  update: (id: number, data: Record<string, unknown>) => request.put(`/teacher/topics/${id}`, data),
  remove: (id: number) => request.delete(`/teacher/topics/${id}`),
  publish: (id: number) => request.post(`/teacher/topics/${id}/publish`),
  close: (id: number) => request.post(`/teacher/topics/${id}/close`),
  adminList: (params: Record<string, unknown>) => request.get('/admin/topics', { params }),
  adminSummary: () => request.get('/admin/topics/summary'),
  adminCreate: (data: Record<string, unknown>) => request.post('/admin/topics', data),
  adminUpdate: (id: number, data: Record<string, unknown>) => request.put(`/admin/topics/${id}`, data),
  adminPublish: (id: number) => request.post(`/admin/topics/${id}/publish`),
  adminRemove: (id: number) => request.delete(`/admin/topics/${id}`),
}

// ---------- 选题 ----------
export const selectionApi = {
  apply: (data: { topic_id: number; reason: string }) => request.post('/student/selections', data),
  mine: () => request.get('/student/selections'),
  cancel: (id: number) => request.post(`/student/selections/${id}/cancel`),
  teacherList: (params: Record<string, unknown>) => request.get('/teacher/selections', { params }),
  approve: (id: number, comment?: string) => request.post(`/teacher/selections/${id}/approve`, { comment }),
  reject: (id: number, comment?: string) => request.post(`/teacher/selections/${id}/reject`, { comment }),
  adminList: (params: Record<string, unknown>) => request.get('/admin/selections', { params }),
  adminApprove: (id: number, comment?: string) => request.post(`/admin/selections/${id}/approve`, { comment }),
  adminReject: (id: number, comment?: string) => request.post(`/admin/selections/${id}/reject`, { comment }),
}

// ---------- 阶段报告(开题/中期/终期 通用) ----------
export interface StageApiConfig {
  studentBase: string
  teacherBase: string
  adminBase: string
}

export const STAGE_API: Record<string, StageApiConfig> = {
  opening: {
    studentBase: '/student/opening-report',
    teacherBase: '/teacher/opening-reports',
    adminBase: '/admin/opening-reports',
  },
  midterm: {
    studentBase: '/student/midterm',
    teacherBase: '/teacher/midterms',
    adminBase: '/admin/midterms',
  },
  final: {
    studentBase: '/student/final-defense',
    teacherBase: '/teacher/final-defenses',
    adminBase: '/admin/final-defenses',
  },
}

export const stageApi = {
  view: (stage: string) => request.get(STAGE_API[stage].studentBase),
  save: (stage: string, content: Record<string, string>) => request.post(STAGE_API[stage].studentBase, { content }),
  submit: (stage: string) => request.post(`${STAGE_API[stage].studentBase}/submit`),
  versions: (stage: string) => request.get(`${STAGE_API[stage].studentBase}/versions`),
  teacherList: (stage: string, params: Record<string, unknown>) => request.get(STAGE_API[stage].teacherBase, { params }),
  teacherDetail: (stage: string, id: number) => request.get(`${STAGE_API[stage].teacherBase}/${id}`),
  approve: (stage: string, id: number, comment?: string, score?: number) =>
    request.post(`${STAGE_API[stage].teacherBase}/${id}/approve`, { comment, score }),
  reject: (stage: string, id: number, comment?: string) =>
    request.post(`${STAGE_API[stage].teacherBase}/${id}/reject`, { comment }),
  adminList: (stage: string, params: Record<string, unknown>) => request.get(STAGE_API[stage].adminBase, { params }),
  adminDetail: (stage: string, id: number) => request.get(`${STAGE_API[stage].adminBase}/${id}`),
  adminApprove: (stage: string, id: number, comment?: string) =>
    request.post(`${STAGE_API[stage].adminBase}/${id}/approve`, { comment }),
  adminReject: (stage: string, id: number, comment?: string) =>
    request.post(`${STAGE_API[stage].adminBase}/${id}/reject`, { comment }),
}

// ---------- 毕业材料 ----------
export const materialApi = {
  mine: () => request.get('/student/material'),
  uploadFile: (file: File, material_type: string) => {
    const form = new FormData()
    form.append('file', file)
    return request.post(`/student/material/files?material_type=${material_type}`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  removeFile: (id: number) => request.delete(`/student/material/files/${id}`),
  submit: () => request.post('/student/material/submit'),
  teacherList: (params: Record<string, unknown>) => request.get('/teacher/materials', { params }),
  teacherDetail: (id: number) => request.get(`/teacher/materials/${id}`),
  approve: (id: number, comment?: string) => request.post(`/teacher/materials/${id}/approve`, { comment }),
  reject: (id: number, comment?: string) => request.post(`/teacher/materials/${id}/reject`, { comment }),
  adminList: (params: Record<string, unknown>) => request.get('/admin/materials', { params }),
  adminStats: (academicYear?: string) => request.get('/admin/materials/stats', { params: { academic_year: academicYear } }),
  adminFilters: () => request.get('/admin/materials/filters'),
  adminApprove: (id: number, comment?: string) => request.post(`/admin/materials/${id}/approve`, { comment }),
  adminReject: (id: number, comment?: string) => request.post(`/admin/materials/${id}/reject`, { comment }),
}

// ---------- 进度 ----------
export const progressApi = {
  mine: () => request.get('/student/progress'),
  teacherStudents: (params: Record<string, unknown>) => request.get('/teacher/students/progress', { params }),
  teacherStudentDetail: (id: number) => request.get(`/teacher/students/${id}/progress`),
  sendReminder: (id: number, data: { title: string; content: string }) =>
    request.post(`/teacher/students/${id}/reminders`, data),
  adminList: (params: Record<string, unknown>) => request.get('/admin/progress', { params }),
  adminSummary: (academicYear?: string) => request.get('/admin/progress/summary', { params: { academic_year: academicYear } }),
  adminRemind: (data: { student_ids: number[]; title: string; content: string }) => request.post('/admin/progress/reminders', data),
}
