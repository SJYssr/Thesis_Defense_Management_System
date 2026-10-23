import request from './request'
import { clampPageSize, type AiDocument, type AiDocumentType, type ApiEnvelope, type PageResult, type UploadResult } from '@/types/api'

export const aiApi = {
  /**
   * P3: GET /ai/documents 已改为 page/size 分页(size ≤ 100)，返回 { items, page, page_size, total }。
   * 注意后端这里的分页参数名是 size(不是 page_size)，且 size 上限 100。
   */
  listDocuments: (page = 1, size = 20) =>
    request.get<unknown, ApiEnvelope<PageResult<AiDocument>>>('/ai/documents', {
      params: { page: Math.max(1, Math.floor(page) || 1), size: clampPageSize(size) },
    }),
  ragConfig: () => request.get('/ai/rag/config'),
  indexDocument: (id: number) => request.post(`/ai/documents/${id}/index`),
  documentStatus: (id: number) => request.get(`/ai/documents/${id}/status`),
  indexReports: () => request.post('/ai/documents/index-reports'),
  /**
   * document_types 在后端是枚举强校验(list[AIDocumentType])，传非法值直接 42200，
   * 所以这里只允许枚举值；undefined 表示不过滤。
   */
  ragQuery: (data: { query: string; top_k?: number; document_types?: AiDocumentType[] }) =>
    request.post('/ai/rag/query', data),
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
  exportPptx: (data: unknown) => request.post('/ai/ppt-outline/export', data, { responseType: 'blob' }),
  analyseProgress: (studentId: number) => request.post(`/ai/progress/student/${studentId}/analyse`),
}

export const dashboardApi = {
  home: () => request.get('/dashboard/home'),
  stats: () => request.get('/dashboard/stats'),
  analytics: () => request.get('/dashboard/analytics'),
  adminActivity: () => request.get('/dashboard/admin-activity'),
  teacherActivity: () => request.get('/dashboard/teacher-activity'),
}

/** P7-A: POST/PUT /admin/announcements 支持 status(DRAFT|PUBLISHED, 缺省 PUBLISHED) */
export interface AnnouncementPayload {
  title: string
  content: string
  priority?: 'NORMAL' | 'IMPORTANT' | 'URGENT'
  expire_time?: string | null
  status?: 'DRAFT' | 'PUBLISHED'
  [key: string]: unknown
}

export const announcementApi = {
  list: (params: Record<string, unknown>) => request.get('/announcements', { params }),
  detail: (id: number) => request.get(`/announcements/${id}`),
  adminList: (params: Record<string, unknown>) => request.get('/admin/announcements', { params }),
  /** 传 status: 'DRAFT' 保存为草稿, 缺省 = 直接发布 */
  create: (data: AnnouncementPayload) => request.post('/admin/announcements', data),
  /**
   * 注意: 已发布的公告不能改回草稿(后端 40003「公告已发布, 不能退回草稿」),
   * 下线请调用 remove() —— 它的语义是 transition(offline) 而非物理删除。
   */
  update: (id: number, data: AnnouncementPayload) => request.put(`/admin/announcements/${id}`, data),
  /** 语义 = 下线(OFFLINE), 不是物理删除 */
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
  /**
   * P7-A: 新增可选业务挂载字段 business_type(≤32) / business_id(≥1),
   * 响应带 business_type/business_id/daily_used_bytes;
   * 当日配额用尽返回 40013(与单文件超限 40010 是不同错误, 文案不可合并)。
   */
  file: (file: File, options: { business_type?: string; business_id?: number | string } = {}) => {
    const form = new FormData()
    form.append('file', file)
    if (options.business_type) form.append('business_type', options.business_type.slice(0, 32))
    if (options.business_id !== undefined && options.business_id !== null && options.business_id !== '') {
      form.append('business_id', String(options.business_id))
    }
    return request.post<unknown, ApiEnvelope<UploadResult>>('/files/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}
