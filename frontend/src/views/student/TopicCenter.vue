<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { topicApi, selectionApi } from '@/api/business'

const loading = ref(false)
const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 12, keyword: '', difficulty: '' })

const detailVisible = ref(false)
const current = ref<any>(null)
const applyVisible = ref(false)
const applyForm = reactive({ topic_id: 0, reason: '' })
const applying = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await topicApi.list(query)
    items.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function openDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

function openApply(row: any) {
  applyForm.topic_id = row.id
  applyForm.reason = ''
  applyVisible.value = true
}

async function submitApply() {
  if (applyForm.reason.trim().length < 10) {
    ElMessage.warning('选题理由至少 10 个字')
    return
  }
  applying.value = true
  try {
    await selectionApi.apply({ ...applyForm })
    ElMessage.success('选题申请已提交, 请等待教师审核')
    applyVisible.value = false
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    applying.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">题目中心</h3>
      <span style="color: #909399; font-size: 13px">共 {{ total }} 个可选题目</span>
    </div>
    <div class="filter-bar">
      <el-input v-model="query.keyword" placeholder="搜索题目名称/描述" clearable style="width: 260px" @keyup.enter="load" />
      <el-select v-model="query.difficulty" placeholder="难度" clearable style="width: 120px">
        <el-option label="简单" value="EASY" />
        <el-option label="中等" value="MEDIUM" />
        <el-option label="困难" value="HARD" />
      </el-select>
      <el-button type="primary" @click="load">查询</el-button>
    </div>

    <el-row :gutter="16">
      <el-col v-for="t in items" :key="t.id" :span="8" style="margin-bottom: 16px">
        <el-card shadow="hover" style="height: 100%">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-weight: 600; font-size: 14px">{{ t.title }}</span>
            </div>
          </template>
          <p class="topic-desc">{{ t.description || '暂无描述' }}</p>
          <div style="color: #909399; font-size: 13px; margin-bottom: 10px">
            <div><el-icon style="vertical-align: -2px"><Avatar /></el-icon> {{ t.teacher_name }} {{ t.teacher_title ? `(${t.teacher_title})` : '' }}</div>
            <div style="margin-top: 4px">
              难度: {{ ({ EASY: '简单', MEDIUM: '中等', HARD: '困难' } as Record<string, string>)[t.difficulty || ''] || '-' }} ·
              名额: {{ t.approved_count }}/{{ t.max_students }}
            </div>
          </div>
          <div style="display: flex; gap: 8px">
            <el-button size="small" @click="openDetail(t)">详情</el-button>
            <el-button size="small" type="primary" :disabled="t.approved_count >= t.max_students" @click="openApply(t)">
              {{ t.approved_count >= t.max_students ? '名额已满' : '申请选题' }}
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-empty v-if="!loading && !items.length" description="暂无可选题目" />
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-dialog v-model="detailVisible" :title="current?.title" width="640px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="指导教师">{{ current?.teacher_name }}</el-descriptions-item>
        <el-descriptions-item label="面向专业">{{ current?.major_name || '不限' }}</el-descriptions-item>
        <el-descriptions-item label="难度">{{ current?.difficulty }}</el-descriptions-item>
        <el-descriptions-item label="名额">{{ current?.approved_count }}/{{ current?.max_students }}</el-descriptions-item>
      </el-descriptions>
      <h4>题目描述</h4>
      <p style="white-space: pre-wrap; color: #606266">{{ current?.description || '无' }}</p>
      <h4>题目要求</h4>
      <p style="white-space: pre-wrap; color: #606266">{{ current?.requirements || '无' }}</p>
    </el-dialog>

    <el-dialog v-model="applyVisible" title="申请选题" width="520px">
      <el-form label-width="80px">
        <el-form-item label="选题理由">
          <el-input v-model="applyForm.reason" type="textarea" :rows="5" maxlength="2000" show-word-limit
                    placeholder="请说明您选择该题目的原因、相关基础与初步设想(至少10字)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="applyVisible = false">取消</el-button>
        <el-button type="primary" :loading="applying" @click="submitApply">提交申请</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.topic-desc {
  color: #606266;
  font-size: 13px;
  height: 60px;
  margin: 0 0 10px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
</style>
