<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { stageApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'
import ReviewHistory from '@/components/ReviewHistory.vue'

const route = useRoute()
const stage = computed(() => (route.meta.stage as string) || 'opening')

interface FieldConf {
  key: string
  label: string
  required: boolean
  textarea: boolean
  placeholder?: string
}

const STAGE_FIELDS: Record<string, { title: string; fields: FieldConf[] }> = {
  opening: {
    title: '开题报告',
    fields: [
      { key: 'title', label: '报告题目', required: true, textarea: false },
      { key: 'research_background', label: '研究背景', required: true, textarea: true },
      { key: 'research_significance', label: '研究意义', required: false, textarea: true },
      { key: 'research_content', label: '研究内容', required: true, textarea: true },
      { key: 'research_method', label: '研究方法', required: true, textarea: true },
      { key: 'technical_route', label: '技术路线', required: false, textarea: true },
      { key: 'schedule_plan', label: '进度安排', required: true, textarea: true },
      { key: 'references', label: '参考文献', required: false, textarea: true },
    ],
  },
  midterm: {
    title: '中期报告',
    fields: [
      { key: 'progress_description', label: '总体进度说明', required: true, textarea: true },
      { key: 'completed_content', label: '已完成内容', required: true, textarea: true },
      { key: 'remaining_content', label: '剩余工作', required: false, textarea: true },
      { key: 'existing_problems', label: '存在问题', required: false, textarea: true },
      { key: 'next_plan', label: '下一步计划', required: true, textarea: true },
    ],
  },
  final: {
    title: '终期答辩',
    fields: [
      { key: 'summary', label: '工作总结', required: true, textarea: true },
      { key: 'innovation', label: '创新点', required: false, textarea: true },
      { key: 'technical_solution', label: '技术方案', required: true, textarea: true },
      { key: 'test_result', label: '测试结果', required: true, textarea: true },
    ],
  },
}

const conf = computed(() => STAGE_FIELDS[stage.value])
const view = ref<any>(null)
const history = ref<any[]>([])
const versions = ref<any[]>([])
const saving = ref(false)
const submitting = ref(false)
const form = reactive<Record<string, string>>({})

const canEdit = computed(() => view.value?.can_edit ?? true)

async function load() {
  const res = await stageApi.view(stage.value)
  view.value = res.data
  history.value = res.data.review_history || []
  for (const f of conf.value.fields) {
    form[f.key] = res.data.content?.[f.key] || ''
  }
  const v = await stageApi.versions(stage.value)
  versions.value = v.data
}

async function save() {
  saving.value = true
  try {
    await stageApi.save(stage.value, { ...form })
    ElMessage.success('草稿已保存')
    await load()
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

async function submit() {
  submitting.value = true
  try {
    await stageApi.save(stage.value, { ...form })
    await stageApi.submit(stage.value)
    ElMessage.success('已提交, 等待教师审核')
    await load()
  } catch {
    /* 已提示 */
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">{{ conf.title }}</h3>
      <div>
        <StatusTag v-if="view" :status="view.status" />
        <span v-if="view?.current_version" style="margin-left: 8px; color: #909399; font-size: 13px">
          当前版本 v{{ view.current_version }}
        </span>
      </div>
    </div>

    <el-alert v-if="view?.status === 'REJECTED' || view?.status === 'RESUBMIT_REQUIRED'" type="error" :closable="false"
              style="margin-bottom: 16px" :title="`审核意见: ${view?.review_comment || '无'}`" />

    <el-form label-position="top">
      <el-row :gutter="16">
        <el-col v-for="f in conf.fields" :key="f.key" :span="f.textarea ? 24 : 12">
          <el-form-item :label="f.label" :required="f.required">
            <el-input v-if="f.textarea" v-model="form[f.key]" type="textarea" :rows="4" :disabled="!canEdit"
                      :placeholder="`请输入${f.label}`" />
            <el-input v-else v-model="form[f.key]" :disabled="!canEdit" :placeholder="`请输入${f.label}`" />
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <div v-if="canEdit" style="display: flex; gap: 12px; justify-content: flex-end">
      <el-button :loading="saving" @click="save">保存草稿</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">提交审核</el-button>
    </div>
    <el-alert v-else type="info" :closable="false" style="margin-top: 8px"
              :title="view?.status === 'APPROVED' ? '该阶段已审核通过' : '材料已提交, 等待教师审核'" />
  </div>

  <el-row :gutter="16" style="margin-top: 16px">
    <el-col :span="12">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">审核历史</h3>
        <ReviewHistory :records="history" />
      </div>
    </el-col>
    <el-col :span="12">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">历史版本</h3>
        <el-empty v-if="!versions.length" description="暂无提交版本" :image-size="70" />
        <el-collapse v-else>
          <el-collapse-item v-for="v in versions" :key="v.id" :title="`v${v.version_no} · ${(v.submitted_at || '').replace('T', ' ').slice(0, 16)}`">
            <div v-for="f in conf.fields" :key="f.key" style="margin-bottom: 8px">
              <strong>{{ f.label }}:</strong>
              <p style="white-space: pre-wrap; color: #606266; margin: 4px 0 0">{{ v.content?.[f.key] || '无' }}</p>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-col>
  </el-row>
</template>
