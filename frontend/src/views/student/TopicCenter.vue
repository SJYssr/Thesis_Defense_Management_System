<template>
  <div class="topic-center">
    <header class="page-heading"><h1>题目中心</h1><p>浏览全部已发布题目，每个学生仅可最终选择一个题目</p></header>

    <!-- 筛选区 -->
    <div class="filter-card">
      <div class="filter-item">
        <span class="filter-label">关键词</span>
        <el-input v-model="query.keyword" placeholder="题目 / 教师 / 技术栈" clearable class="filter-input" @keyup.enter="onSearch" />
      </div>
      <div class="filter-item">
        <span class="filter-label">专业</span>
        <el-select v-model="query.major_id" placeholder="全部专业" clearable class="filter-select">
          <el-option v-for="m in majors" :key="m.id" :label="m.name" :value="m.id" />
        </el-select>
      </div>
      <div class="filter-item">
        <span class="filter-label">难度</span>
        <el-select v-model="query.difficulty" placeholder="全部难度" clearable class="filter-select">
          <el-option label="简单" value="EASY" />
          <el-option label="中等" value="MEDIUM" />
          <el-option label="困难" value="HARD" />
        </el-select>
      </div>
      <div class="filter-item">
        <span class="filter-label">题目类型</span>
        <el-select v-model="topicType" placeholder="全部类型" clearable class="filter-select">
          <el-option v-for="t in typeOptions" :key="t" :label="t" :value="t" />
        </el-select>
      </div>
      <div class="filter-actions">
        <el-button type="primary" @click="onSearch">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </div>

    <!-- 题目卡片 -->
    <div v-loading="loading" class="topic-grid">
      <article v-for="t in visibleItems" :key="t.id" class="topic-card">
        <h3 class="tc-title" @click="openDetail(t)">{{ t.title }}</h3>
        <p class="tc-meta">指导教师：{{ t.teacher_name || '—' }}<span class="tc-dot" />类型：{{ t.topic_type || '毕业设计' }}</p>
        <div class="tc-tags">
          <span class="tc-chip">{{ t.topic_type || '综合' }}</span>
          <span class="tc-chip diff">{{ ({ EASY: '简单', MEDIUM: '中等', HARD: '困难' } as Record<string, string>)[t.difficulty || ''] || '中等' }}</span>
        </div>
        <p class="tc-desc">题目简介：{{ t.description || '暂无简介' }}</p>
        <div class="tc-footer">
          <span class="tc-count">已选人数：{{ t.approved_count }} / {{ t.max_students }}</span>
          <el-button v-if="t.approved_count >= t.max_students" disabled>名额已满</el-button>
          <el-button v-else type="primary" @click="openApply(t)">申请选题</el-button>
        </div>
      </article>
    </div>
    <el-empty v-if="!loading && !visibleItems.length" description="暂无可选题目" />

    <div class="pagination-bar">
      <span class="page-total">共 {{ total }} 个题目</span>
      <el-pagination
        v-model:current-page="query.page" :page-size="query.page_size" :total="total"
        background layout="prev, pager, next, jumper" class="pager" @current-change="load"
      />
    </div>

    <!-- 题目详情 -->
    <el-dialog v-model="detailVisible" :title="current?.title" width="640px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="指导教师">{{ current?.teacher_name }}{{ current?.teacher_title ? `（${current.teacher_title}）` : '' }}</el-descriptions-item>
        <el-descriptions-item label="面向专业">{{ current?.major_name || '不限' }}</el-descriptions-item>
        <el-descriptions-item label="题目类型">{{ current?.topic_type || '毕业设计' }}</el-descriptions-item>
        <el-descriptions-item label="难度">{{ ({ EASY: '简单', MEDIUM: '中等', HARD: '困难' } as Record<string, string>)[current?.difficulty || ''] || '中等' }}</el-descriptions-item>
        <el-descriptions-item label="已选人数">{{ current?.approved_count }}/{{ current?.max_students }}</el-descriptions-item>
        <el-descriptions-item label="发布时间">{{ (current?.publish_time || '—').slice(0, 10) }}</el-descriptions-item>
      </el-descriptions>
      <h4 class="detail-sub">题目简介</h4>
      <p class="detail-text">{{ current?.description || '无' }}</p>
      <h4 class="detail-sub">题目要求</h4>
      <p class="detail-text">{{ current?.requirements || '无' }}</p>
    </el-dialog>

    <!-- 申请选题 -->
    <el-dialog v-model="applyVisible" title="申请选题" width="560px">
      <p class="apply-topic">{{ applyTarget?.title }}</p>
      <el-input
        v-model="applyForm.reason" type="textarea" :rows="5" maxlength="2000" show-word-limit
        placeholder="请说明您选择该题目的原因、相关基础与初步设想（至少 10 字）"
      />
      <template #footer>
        <el-button @click="applyVisible = false">取消</el-button>
        <el-button type="primary" :loading="applying" @click="submitApply">提交申请</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { topicApi, selectionApi } from '@/api/business'
import { userAdminApi } from '@/api/common'

const loading = ref(false)
const items = ref<any[]>([])
const majors = ref<{ id: number; name: string }[]>([])
const total = ref(0)
const topicType = ref('')
const query = reactive({ page: 1, page_size: 12, keyword: '', difficulty: '', major_id: null as number | null })

const typeOptions = computed(() =>
  [...new Set(['软件开发', '算法研究', '应用研究', '硬件设计', ...items.value.map(t => t.topic_type || '').filter(Boolean)])])
const visibleItems = computed(() =>
  topicType.value ? items.value.filter(t => (t.topic_type || '') === topicType.value) : items.value)

async function load() {
  loading.value = true
  try {
    const res = await topicApi.list({
      page: query.page, page_size: query.page_size,
      keyword: query.keyword || undefined,
      difficulty: query.difficulty || undefined,
      major_id: query.major_id || undefined,
    })
    items.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function onSearch() { query.page = 1; load() }
function onReset() {
  Object.assign(query, { page: 1, keyword: '', difficulty: '', major_id: null })
  topicType.value = ''
  load()
}

const detailVisible = ref(false)
const current = ref<any>(null)
function openDetail(row: any) { current.value = row; detailVisible.value = true }

const applyVisible = ref(false)
const applyTarget = ref<any>(null)
const applyForm = reactive({ topic_id: 0, reason: '' })
const applying = ref(false)

function openApply(row: any) {
  applyTarget.value = row
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
    ElMessage.success('选题申请已提交，请等待教师审核')
    applyVisible.value = false
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    applying.value = false
  }
}

onMounted(async () => {
  load()
  try { majors.value = (await userAdminApi.majors()).data } catch { /* 忽略 */ }
})
</script>

<style scoped>
.page-heading { margin-bottom: 18px; }
.page-heading h1 { font-size: 26px; color: #1f2d3d; margin: 0 0 6px; }
.page-heading p { color: #93a0b4; font-size: 13px; margin: 0; }

/* 筛选区 */
.filter-card {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  background: #fff;
  border-radius: 8px;
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 16px;
}
.filter-item { display: flex; align-items: center; gap: 8px; }
.filter-label { font-size: 13px; color: #5a6b80; white-space: nowrap; }
.filter-input { width: 220px; }
.filter-select { width: 150px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }

/* 题目卡片 */
.topic-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  min-height: 120px;
}
.topic-card {
  background: #fff;
  border-radius: 10px;
  padding: 20px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  display: flex;
  flex-direction: column;
  transition: box-shadow 0.2s;
}
.topic-card:hover { box-shadow: 0 4px 16px rgba(15, 46, 93, 0.12); }
.tc-title {
  font-size: 16px;
  color: #1f2d3d;
  margin: 0 0 10px;
  line-height: 1.45;
  cursor: pointer;
}
.tc-title:hover { color: #1878fd; }
.tc-meta { font-size: 12px; color: #8a97ad; margin: 0 0 12px; }
.tc-dot { display: inline-block; width: 3px; height: 3px; border-radius: 50%; background: #c2cbd8; margin: 0 8px; vertical-align: middle; }
.tc-tags { display: flex; gap: 8px; margin-bottom: 12px; }
.tc-chip {
  display: inline-block;
  color: #1878fd;
  background: #e3effe;
  border-radius: 4px;
  font-size: 11px;
  line-height: 20px;
  padding: 1px 10px;
}
.tc-chip.diff { color: #0e9f6e; background: #def7ec; }
.tc-desc {
  flex: 1;
  color: #5a6b80;
  font-size: 12.5px;
  line-height: 1.7;
  margin: 0 0 14px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tc-footer { display: flex; align-items: center; justify-content: space-between; }
.tc-count { font-size: 12.5px; color: #5a6b80; }

/* 分页 */
.pagination-bar { display: flex; align-items: center; padding: 16px 2px 0; }
.page-total { font-size: 12.5px; color: #8a97a8; margin-right: 12px; }
.pager { margin-left: auto; }

/* 详情与申请 */
.detail-sub { margin: 16px 0 8px; color: #1f2d3d; }
.detail-text {
  white-space: pre-wrap;
  color: #606266;
  background: #f8fafc;
  border-radius: 6px;
  padding: 12px;
  margin: 0;
  line-height: 1.7;
}
.apply-topic { font-size: 14px; font-weight: 600; color: #1f2d3d; margin: 0 0 12px; }

@media (max-width: 1100px) {
  .topic-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-actions { margin-left: 0; }
}
@media (max-width: 640px) {
  .topic-grid { grid-template-columns: minmax(0, 1fr); }
  .filter-item { flex: 1 1 100%; }
  .filter-item :deep(.el-input), .filter-item :deep(.el-select) { width: 100%; }
  .pagination-bar { flex-wrap: wrap; gap: 10px; }
  .pager { margin-left: 0; max-width: 100%; overflow-x: auto; }
}
</style>
