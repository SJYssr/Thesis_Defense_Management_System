<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import { refreshPublicConfig } from '@/stores/app'

const loading = ref(false)
const saving = ref(false)
const form = reactive({
  system_name: '',
  school_name: '',
  ai_defense_enabled: true,
  ai_rate_limit_per_minute: 10,
  max_upload_size_mb: 50,
  /** P7-A 新增配置项(max_upload_daily_mb, 默认 200, 整数, 已进后端 sysconfig FIELDS) */
  max_upload_daily_mb: 200,
  ai_llm_model: '',
})

const BOOL_KEYS = ['ai_defense_enabled']
const INT_KEYS = ['ai_rate_limit_per_minute', 'max_upload_size_mb', 'max_upload_daily_mb']

const modelInfo = ref<{
  llm_provider: string
  llm_openai_mode: boolean
  llm_model_env: string
  llm_model_effective: string
  embedding_provider: string
  embedding_dim: number
  embedding_model: string | null
} | null>(null)

async function load() {
  loading.value = true
  try {
    const items: any[] = (await request.get('/admin/system-configs')).data
    for (const item of items) {
      if (BOOL_KEYS.includes(item.key)) (form as any)[item.key] = item.value === 'true'
      else if (INT_KEYS.includes(item.key)) (form as any)[item.key] = Number(item.value) || 0
      else (form as any)[item.key] = item.value ?? ''
    }
    await loadModelInfo()
  } finally {
    loading.value = false
  }
}

async function loadModelInfo() {
  try {
    modelInfo.value = (await request.get('/admin/ai-model-info')).data
  } catch {
    modelInfo.value = null
  }
}

async function save() {
  saving.value = true
  try {
    const items: Record<string, string> = {}
    for (const [key, value] of Object.entries(form)) {
      items[key] = String(value)
    }
    await request.put('/admin/system-configs', { items })
    await refreshPublicConfig()
    ElMessage.success('配置已保存并即时生效')
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-card settings-page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1 class="page-title">系统配置</h1>
        <p class="hint">系统名称、AI 功能开关与资源上限，保存后即时生效</p>
      </div>
      <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
    </div>

    <div class="settings-grid">
      <section class="settings-block">
        <h3 class="block-title">基础信息</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="系统名称">
            <el-input v-model="form.system_name" maxlength="50" show-word-limit placeholder="登录页与侧栏展示的名称" />
          </el-form-item>
          <el-form-item label="学校名称">
            <el-input v-model="form.school_name" maxlength="50" show-word-limit placeholder="例如：XX大学（可选）" />
          </el-form-item>
        </el-form>
      </section>

      <section class="settings-block">
        <h3 class="block-title">AI 功能</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="AI 功能开关">
            <el-switch v-model="form.ai_defense_enabled" active-text="开启" inactive-text="关闭" />
            <div class="field-hint">关闭后学生端 AI 模拟答辩、知识问答、PPT 大纲等接口将统一停用</div>
          </el-form-item>
          <el-form-item label="调用频率上限">
            <el-input-number v-model="form.ai_rate_limit_per_minute" :min="1" :max="999" />
            <span class="unit">次 / 分钟 / 用户</span>
          </el-form-item>
        </el-form>
      </section>

      <section class="settings-block">
        <h3 class="block-title">AI 模型（roadmap 八十五章）</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="LLM 模型覆盖">
            <el-input v-model="form.ai_llm_model" maxlength="100" clearable
              :placeholder="`留空使用服务器默认（${modelInfo?.llm_model_env || '环境变量'}）`" />
            <div class="field-hint">
              当前生效：{{ modelInfo?.llm_model_effective || '-' }}（供应商 {{ modelInfo?.llm_provider || '-' }}），
              保存后即时生效{{ modelInfo && !modelInfo.llm_openai_mode ? '；当前为离线演示模式，配置 API Key 后覆盖才会实际切换模型' : '' }}
            </div>
          </el-form-item>
          <el-form-item label="Embedding">
            <div class="readonly-line">
              {{ modelInfo?.embedding_model || modelInfo?.embedding_provider || '-' }}
              <span v-if="modelInfo?.embedding_model" class="dim-tag">{{ modelInfo.embedding_dim }} 维</span>
            </div>
            <div class="field-hint">Embedding 模型切换会使既有向量索引失效，需修改服务器环境变量并重建知识库索引，不提供在线修改</div>
          </el-form-item>
        </el-form>
      </section>

      <section class="settings-block">
        <h3 class="block-title">资源限制</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="上传大小上限">
            <el-input-number v-model="form.max_upload_size_mb" :min="1" :max="2048" />
            <span class="unit">MB / 单文件</span>
            <div class="field-hint">对论文、材料等所有上传文件生效</div>
          </el-form-item>
          <el-form-item label="每日上传上限">
            <el-input-number v-model="form.max_upload_daily_mb" :min="1" :max="102400" />
            <span class="unit">MB / 每人每日</span>
            <div class="field-hint">按人按自然日累计；用尽后上传返回 40013（与单文件超限 40010 是不同错误）</div>
          </el-form-item>
        </el-form>
      </section>
    </div>

    <el-alert type="info" :closable="false" show-icon
      title="配置修改即时生效（Redis 缓存在保存时失效）；LLM API Key 与 Embedding 供应商仍通过服务器环境变量配置。" />
  </div>
</template>

<style scoped>
.settings-page { max-width: 980px; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 22px; margin-bottom: 20px; }
.settings-block { border: 1px solid #eef0f5; border-radius: 8px; padding: 18px; }
.unit { margin-left: 10px; color: #8a93a5; font-size: 12px; }
.field-hint { width: 100%; color: #9aa2b0; font-size: 12px; line-height: 1.6; margin-top: 4px; }
.readonly-line { color: #2c3e50; font-size: 14px; }
.dim-tag { margin-left: 8px; font-size: 12px; color: #93a1b4; }
@media (max-width: 800px) {
  .settings-grid { grid-template-columns: 1fr; }
  .settings-page :deep(.el-form-item__label) { width: 96px !important; }
}
</style>
