import request from './request'
import type { ApiEnvelope, PageResult, StageTask, StageTaskVersion } from '@/types/api'

// ---------- 阶段任务(P1) ----------
export const taskApi = {
  list: (params: Record<string, unknown>) => request.get<unknown, ApiEnvelope<PageResult<StageTask>>>('/stage-tasks', { params }),
  /**
   * P7-A(D59): POST 的 status 缺省已由 PUBLISHED 改为 DRAFT，
   * 需要直接发布的调用方必须显式传 status: 'PUBLISHED'。
   */
  create: (data: Record<string, unknown>) => request.post<unknown, ApiEnvelope<{ id: number }>>('/stage-tasks', data),
  /** P7-A(D59): PUT 不再接受 status, 状态流转一律走 publish/close */
  update: (id: number, data: Record<string, unknown>) => request.put(`/stage-tasks/${id}`, data),
  /** DRAFT -> PUBLISHED */
  publish: (id: number) => request.post(`/stage-tasks/${id}/publish`),
  /** PUBLISHED -> CLOSED */
  close: (id: number) => request.post(`/stage-tasks/${id}/close`),
  /** P7-A: 该接口已由数组改为分页对象 { items, page, page_size, total } */
  versions: (id: number, params: { page?: number; page_size?: number } = { page: 1, page_size: 20 }) =>
    request.get<unknown, ApiEnvelope<PageResult<StageTaskVersion>>>(`/stage-tasks/${id}/versions`, { params }),
  mine: () => request.get('/stage-tasks/mine'),
}

// ---------- 答辩排期(P1) ----------
export const defenseApi = {
  adminList: (params: Record<string, unknown>) => request.get('/defense-sessions', { params }),
  create: (data: Record<string, unknown>) => request.post('/defense-sessions', data),
  detail: (id: number) => request.get(`/defense-sessions/${id}`),
  patch: (id: number, data: Record<string, unknown>) => request.patch(`/defense-sessions/${id}`, data),
  validate: (id: number) => request.post(`/defense-sessions/${id}/validate`),
  publish: (id: number) => request.post(`/defense-sessions/${id}/publish`),
  // PUBLISHED -> IN_PROGRESS; 非法状态由后端状态机返回 40003(带状态与动作, 直接展示后端 message)
  start: (id: number) => request.post(`/defense-sessions/${id}/start`),
  // PUBLISHED|IN_PROGRESS -> FINISHED; 非法状态返回 40003
  finish: (id: number) => request.post(`/defense-sessions/${id}/finish`),
  cancel: (id: number, reason: string) => request.post(`/defense-sessions/${id}/cancel`, { change_reason: reason }),
  addAssignments: (id: number, cycle_ids: number[]) => request.post(`/defense-sessions/${id}/assignments`, { cycle_ids }),
  removeAssignment: (sessionId: number, assignmentId: number) =>
    request.delete(`/defense-sessions/${sessionId}/assignments/${assignmentId}`),
  setSeat: (assignmentId: number, data: { seat_status: string; remark?: string }) =>
    request.post(`/defense-assignments/${assignmentId}/seat`, data),
  addExaminer: (id: number, data: { teacher_id: number; examiner_role: string }) =>
    request.post(`/defense-sessions/${id}/examiners`, data),
  removeExaminer: (sessionId: number, examinerId: number) =>
    request.delete(`/defense-sessions/${sessionId}/examiners/${examinerId}`),
  mine: () => request.get('/defense-sessions/mine'),
  acknowledge: (assignmentId: number) => request.post(`/defense-assignments/${assignmentId}/acknowledge`),
}

// ---------- 评阅与评分(P2) ----------
export const reviewApi = {
  myQueue: () => request.get('/defense-assignments/mine'),
  getReview: (assignmentId: number) => request.get(`/defense-assignments/${assignmentId}/review`),
  saveDraft: (assignmentId: number, data: { scores: Record<string, number>; comment?: string }) =>
    request.put(`/defense-assignments/${assignmentId}/review`, data),
  submitScore: (assignmentId: number, data: { scores: Record<string, number>; comment?: string }) =>
    request.post(`/defense-assignments/${assignmentId}/scores/submit`, data),
  results: (sessionId?: number) => request.get('/defense-results', { params: sessionId ? { session_id: sessionId } : {} }),
  generateResult: (assignmentId: number) => request.post('/defense-results/generate', { assignment_id: assignmentId }),
  // PENDING -> READY; 重复复算返回 40003, 因此按钮只在 result.status === 'PENDING' 时出现
  calculate: (resultId: number) => request.post(`/defense-results/${resultId}/calculate`),
  preview: (resultId: number) => request.get(`/defense-results/${resultId}/preview`),
  publish: (resultId: number) => request.post(`/defense-results/${resultId}/publish`),
  withdraw: (resultId: number, reason: string) => request.post(`/defense-results/${resultId}/withdraw`, { reason }),
  reopen: (scoreId: number, reason: string) => request.post(`/examiner-scores/${scoreId}/reopen`, { reason }),
}

// ---------- 评分表配置(P2) ----------
export const rubricApi = {
  list: (params?: Record<string, unknown>) => request.get('/review-rubrics', { params }),
  create: (data: Record<string, unknown>) => request.post('/review-rubrics', data),
  update: (id: number, data: Record<string, unknown>) => request.put(`/review-rubrics/${id}`, data),
  enable: (id: number) => request.post(`/review-rubrics/${id}/enable`),
}

// ---------- 成果归档(P3) ----------
export const archiveApi = {
  search: (params: Record<string, unknown>) => request.get('/archive-packages', { params }),
  detail: (id: number) => request.get(`/archive-packages/${id}`),
  create: (data: Record<string, unknown>) => request.post('/archive-packages', data),
  grantConsent: (id: number, visibility: string) => request.post(`/archive-packages/${id}/consent`, { visibility }),
  revokeConsent: (id: number) => request.delete(`/archive-packages/${id}/consent`),
  revokePackage: (id: number) => request.post(`/archive-packages/${id}/revoke`),
  exportList: () => request.get('/archive-packages/export/list', { responseType: 'blob' }),
}

// ---------- 答辩运营统计(P4) ----------
export const statsApi = {
  defenseStats: () => request.get('/dashboard/defense-stats'),
}
