export interface ReviewQuery { page: number; page_size: number; status: string; keyword: string; studentNo: string; topic: string; dates: string[] }
export async function reviewList(loader: (params: Record<string, unknown>) => Promise<any>, query: ReviewQuery) {
  const params = { status: query.status || undefined, page: query.page, page_size: query.page_size }
  if (!query.keyword && !query.studentNo && !query.topic && !query.dates?.length) return (await loader(params)).data
  // These APIs expose status and pagination; search every page so filters never omit later records.
  const first = (await loader({ ...params, page: 1, page_size: 100 })).data
  const rows = [...first.items]
  for (let page = 2; rows.length < first.total; page++) {
    const next = (await loader({ ...params, page, page_size: 100 })).data.items
    if (!next.length) break
    rows.push(...next)
  }
  const filtered = rows.filter((row: any) => {
    const name = `${row.student_name || ''} ${row.student_no || ''}`
    const topic = row.topic_title || row.content?.title || ''
    const date = (row.submitted_at || row.created_at || '').slice(0, 10)
    return (!query.keyword || name.includes(query.keyword.trim())) && (!query.studentNo || (row.student_no || '').includes(query.studentNo.trim())) && (!query.topic || topic.includes(query.topic.trim())) && (!query.dates?.length || (date >= query.dates[0] && date <= query.dates[1]))
  })
  return { items: filtered.slice((query.page - 1) * query.page_size, query.page * query.page_size), total: filtered.length }
}
