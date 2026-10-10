import { ref } from 'vue'
import request from '@/api/request'

/** 应用级公共状态: 系统名称等公开配置(GET /config/public, 无需登录). */
export const systemName = ref('毕业设计管理系统')
export const schoolName = ref('')
let loaded = false

export async function loadPublicConfig() {
  if (loaded) return
  try {
    const data = (await request.get('/config/public')).data
    if (data?.system_name) systemName.value = data.system_name
    schoolName.value = data?.school_name || ''
    loaded = true
  } catch {
    /* 配置接口失败时保留默认名称 */
  }
}

/** 管理端更新配置后强制刷新展示名. */
export async function refreshPublicConfig() {
  loaded = false
  await loadPublicConfig()
}
