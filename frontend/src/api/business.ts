import request from './request'
import type { ApiEnvelope, OverdueScanResult } from '@/types/api'

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

/**
 * 阶段审核 decision 三值(P1-A 起 ReviewRequest 显式带 decision，不再靠 comment 是否为空推断):
 * - approve: 通过
 * - request_revision: 退回修改
 * - reject: 直接不通过
 */
export type StageDecision = 'approve' | 'reject' | 'request_revision'

export const stageApi = {
  view: (stage: string) => request.get(STAGE_API[stage].studentBase),
  save: (stage: string, content: Record<string, string>) => request.post(STAGE_API[stage].studentBase, { content }),
  submit: (stage: string) => request.post(`${STAGE_API[stage].studentBase}/submit`),
  versions: (stage: string) => request.get(`${STAGE_API[stage].studentBase}/versions`),
  teacherList: (stage: string, params: Record<string, unknown>) => request.get(STAGE_API[stage].teacherBase, { params }),
  teacherDetail: (stage: string, id: number) => request.get(`${STAGE_API[stage].teacherBase}/${id}`),
  // 注意: score 参数已废弃 —— P1-A 起后端 materials/stages 审核不再写 FinalDefense.score，
  // 正式成绩唯一真源是 DefenseResult.score。这里只传 { comment, decision }。
  approve: (stage: string, id: number, comment?: string) =>
    request.post(`${STAGE_API[stage].teacherBase}/${id}/approve`, {
      comment,
      decision: 'approve' as StageDecision,
    }),
  reject: (stage: string, id: number, comment?: string, decision: 'reject' | 'request_revision' = 'reject') =>
    request.post(`${STAGE_API[stage].teacherBase}/${id}/reject`, { comment, decision }),
  adminList: (stage: string, params: Record<string, unknown>) => request.get(STAGE_API[stage].adminBase, { params }),
  adminDetail: (stage: string, id: number) => request.get(`${STAGE_API[stage].adminBase}/${id}`),
  adminApprove: (stage: string, id: number, comment?: string) =>
    request.post(`${STAGE_API[stage].adminBase}/${id}/approve`, {
      comment,
      decision: 'approve' as StageDecision,
    }),
  adminReject: (stage: string, id: number, comment?: string, decision: 'reject' | 'request_revision' = 'reject') =>
    request.post(`${STAGE_API[stage].adminBase}/${id}/reject`, { comment, decision }),
}

// ---------- 毕业材料 ----------
export const materialApi = {
  mine: () => request.get('/student/material'),
  uploadFile: (file: File, material_type: string, business?: { business_type?: string; business_id?: number }) => {
    const form = new FormData()
    form.append('file', file)
    if (business?.business_type) form.append('business_type', business.business_type.slice(0, 32))
    if (business?.business_id) form.append('business_id', String(business.business_id))
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
  /** P7-B: 不传 academic_year 时后端按当前学年(academic_year_for)统计, 不再全学年累计 */
  adminSummary: (academicYear?: string) => request.get('/admin/progress/summary', { params: { academic_year: academicYear } }),
  adminRemind: (data: { student_ids: number[]; title: string; content: string }) => request.post('/admin/progress/reminders', data),
  /** P7-B: 逾期扫描; 响应新增 mode(celery|sync)/queued/batches, 入队路径 scanned=0 */
  overdueScan: () => request.post<unknown, ApiEnvelope<OverdueScanResult>>('/admin/progress/overdue-scan'),
}
