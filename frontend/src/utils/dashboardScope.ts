import type { DashboardScope, DashboardScopeMeta } from '@/types/api'

/** P8: 口径中文标注 —— 后端在 scope_map 里逐字段声明 current_year / all_years */
export const SCOPE_CN: Record<DashboardScope, string> = {
  current_year: '当前学年',
  all_years: '全部学年',
}

/**
 * 取某字段的统计口径: 优先 scope_map 的精确声明(键可以是嵌套路径, 如 'cards.risk_count'),
 * 未声明时回退响应级 scope。
 */
export function scopeOf(meta: DashboardScopeMeta | null | undefined, field: string): DashboardScope | undefined {
  return meta?.scope_map?.[field] ?? meta?.scope
}

/**
 * 渲染口径标注。页面不再写死「当前学年」: 后端把某字段收窄成 current_year 或放开成
 * all_years 时, 文案自动跟随, 不会谎报口径。两者都缺失时按 fallback(默认 current_year)。
 */
export function scopeLabel(
  meta: DashboardScopeMeta | null | undefined,
  field: string,
  fallback: DashboardScope = 'current_year',
): string {
  const scope = scopeOf(meta, field) ?? fallback
  return SCOPE_CN[scope] ?? SCOPE_CN[fallback]
}

/** 完整口径文案, 当前学年附学年标识(如「当前学年 2025-2026」), 全部学年不带 */
export function scopeLabelWithYear(
  meta: DashboardScopeMeta | null | undefined,
  field: string,
  fallback: DashboardScope = 'current_year',
): string {
  const label = scopeLabel(meta, field, fallback)
  const year = meta?.academic_year
  const scope = scopeOf(meta, field) ?? fallback
  return scope === 'all_years' || !year ? label : `${label} ${year}`
}
