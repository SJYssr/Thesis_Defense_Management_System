export interface ReviewQuery { page: number; page_size: number; status: string; keyword: string; studentNo: string; topic: string; dates: string[] }

export interface ReviewListOptions {
  /**
   * 该列表端点是否支持 date_from/date_to。
   * 阶段列表(/teacher|admin/{stage})只有 keyword, 没有日期参数; 置 false 时日期维度既不下推、
   * 也不在前端静默过滤 —— 由页面显式禁用日期控件并给出"暂不支持按时间筛选"的提示。
   */
  supportsDateRange?: boolean
}

/**
 * 把页面上的三个输入框 + 时间区间映射成后端参数。
 * 后端 keyword 是 OR 匹配(学生姓名/学号/题目名称等多字段), 所以 studentNo / topic 合并进 keyword,
 * 不再分开传 —— 分页与 total 全部交由后端计算, 前端不再拉全量。
 */
export function buildReviewParams(query: ReviewQuery, options: ReviewListOptions = {}) {
  const keyword = [query.keyword, query.studentNo, query.topic]
    .map((v) => (v || '').trim())
    .filter(Boolean)
    .join(' ')
  const params: Record<string, unknown> = {
    status: query.status || undefined,
    page: query.page,
    page_size: query.page_size,
    keyword: keyword || undefined,
  }
  if (options.supportsDateRange !== false && query.dates?.length === 2) {
    params.date_from = query.dates[0] || undefined
    params.date_to = query.dates[1] || undefined
  }
  return params
}

/** 单请求下推过滤: 一次调用即可, total 是后端过滤后的命中数。 */
export async function reviewList(
  loader: (params: Record<string, unknown>) => Promise<any>,
  query: ReviewQuery,
  options: ReviewListOptions = {},
) {
  const data = (await loader(buildReviewParams(query, options))).data
  return { items: data?.items ?? [], total: data?.total ?? 0 }
}
