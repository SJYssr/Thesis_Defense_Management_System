<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { stageApi } from '@/api/business'
import MaterialStatus from '@/components/MaterialStatus.vue'

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
    title: '开题报告提交',
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
    title: '中期答辩材料',
    fields: [
      { key: 'progress_description', label: '总体进度说明', required: true, textarea: true },
      { key: 'completed_content', label: '已完成内容', required: true, textarea: true },
      { key: 'remaining_content', label: '剩余工作', required: false, textarea: true },
      { key: 'existing_problems', label: '存在问题', required: false, textarea: true },
      { key: 'next_plan', label: '下一步计划', required: true, textarea: true },
    ],
  },
  final: {
    title: '终期答辩材料',
    fields: [
      { key: 'summary', label: '工作总结', required: true, textarea: true },
      { key: 'innovation', label: '创新点', required: false, textarea: true },
      { key: 'technical_solution', label: '技术方案', required: true, textarea: true },
      { key: 'test_result', label: '测试结果', required: true, textarea: true },
    ],
  },
}

const conf = computed(() => STAGE_FIELDS[stage.value])
const primaryFields = computed(() => stage.value === 'opening' ? conf.value.fields.filter(f => f.required) : conf.value.fields)
const extraFields = computed(() => stage.value === 'opening' ? conf.value.fields.filter(f => !f.required) : [])
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
  const missing = conf.value.fields.find(f => f.required && !form[f.key]?.trim())
  if (missing) { ElMessage.warning(`请填写${missing.label}`); return }
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
  <header class="student-heading"><h1>{{ conf.title }}</h1><p>完善各阶段材料，提交后等待指导教师审核</p></header>
  <div class="student-split">
    <section class="page-card student-form">
      <h3 class="block-title">材料信息</h3>
      <el-form label-position="top">
        <el-form-item v-for="f in primaryFields" :key="f.key" :label="f.label" :required="f.required">
          <el-input v-model="form[f.key]" :type="f.textarea ? 'textarea' : 'text'" :rows="2" :disabled="!canEdit" :placeholder="`请输入${f.label}`" />
        </el-form-item>
        <el-collapse v-if="extraFields.length" class="extra-fields"><el-collapse-item title="补充材料（研究意义、技术路线与参考文献）"><el-form-item v-for="f in extraFields" :key="f.key" :label="f.label"><el-input v-model="form[f.key]" type="textarea" :rows="2" :disabled="!canEdit" :placeholder="`请输入${f.label}`" /></el-form-item></el-collapse-item></el-collapse>
      </el-form>
      <div v-if="canEdit" class="form-actions">
        <el-button :loading="saving" @click="save">保存草稿</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交</el-button>
      </div>
      <el-alert v-else type="info" :closable="false" :title="view?.status === 'APPROVED' ? '该阶段已审核通过' : '材料已提交，等待教师审核'" />
    </section>
    <MaterialStatus :view="view" :versions="versions" :history="history" />
  </div>
</template>
