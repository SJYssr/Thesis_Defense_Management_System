import { ref, watch } from 'vue'
import request from '@/api/request'

/** 应用级公共状态: 系统名称等公开配置(GET /config/public, 无需登录). */
export const systemName = ref('毕业设计管理系统')
export const schoolName = ref('')
/**
 * AI 总开关(P11): 后端 ai_defense_enabled 的镜像。
 *
 * 关掉后后端会让所有调模型的接口返回 40300, 但菜单如果照旧显示, 学生就会
 * "看得见、点进去报错"。这里把开关下发到界面, 菜单据此收敛。
 */
export const aiEnabled = ref(true)
let loaded = false

/**
 * 浏览器标签标题 = 系统名称(+ 学校名称)。
 *
 * index.html 里的 title 只是首屏占位, 真正的名字由「系统设置 → 系统名称」决定;
 * 之前这里从不改写, 管理员改完名称后标签页仍显示模板初始值。
 */
function applyDocumentTitle() {
  const name = systemName.value.trim() || '毕业设计管理系统'
  const school = schoolName.value.trim()
  document.title = school ? `${name} · ${school}` : name
}

watch([systemName, schoolName], applyDocumentTitle)
applyDocumentTitle()

export async function loadPublicConfig() {
  if (loaded) return
  try {
    const data = (await request.get('/config/public')).data
    if (data?.system_name) systemName.value = data.system_name
    schoolName.value = data?.school_name || ''
    // 字段缺失时按"开启"处理: 宁可多显示入口, 也别因一次解析失败把功能藏起来
    // (真被关闭时后端仍会在每个调模型的接口拦下, 不存在越权调用)。
    aiEnabled.value = data?.ai_defense_enabled !== false
    loaded = true
  } catch {
    /* 配置接口失败时保留默认名称与默认开关 */
  }
}

/** 管理端更新配置后强制刷新展示名. */
export async function refreshPublicConfig() {
  loaded = false
  await loadPublicConfig()
}
