<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { materialApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'
import ReviewHistory from '@/components/ReviewHistory.vue'

const route = useRoute()
const isAdmin = computed(() => route.meta.reviewer === 'admin')

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, status: 'SUBMITTED' })

const detailVisible = ref(false)
const current = ref<any>(null)
const comment = ref('')
const reviewing = ref(false)

async function load() {
  const res = isAdmin.value
    ? await materialApi.adminList({ ...query, status: query.status || undefined })
    : await materialApi.teacherList({ ...query, status: query.status || undefined })
  items.value = res.data.items
  total.value = res.data.total
}

async function openDetail(row: any) {
  if (isAdmin.value) {
    current.value = row
  } else {
    current.value = (await materialApi.teacherDetail(row.id)).data
  }
  comment.value = ''
  detailVisible.value = true
}

async function review(approve: boolean) {
  reviewing.value = true
  try {
    if (approve) await materialApi.approve(current.value.id, comment.value || undefined)
    else await materialApi.reject(current.value.id, comment.value || undefined)
    ElMessage.success(approve ? '已通过' : '已驳回')
    detailVisible.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    reviewing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">毕业材料审核</h3>
    </div>
    <el-tabs v-model="query.status" @tab-change="load">
      <el-tab-pane label="待审核" name="SUBMITTED" />
      <el-tab-pane label="已通过" name="APPROVED" />
      <el-tab-pane label="已驳回" name="REJECTED" />
      <el-tab-pane label="全部" name="" />
    </el-tabs>
    <el-table :data="items" border>
      <el-table-column prop="student_name" label="学生" width="100" />
      <el-table-column prop="student_no" label="学号" width="110" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column label="材料数" width="80" align="center">
        <template #default="{ row }">{{ (row.files || []).length }}</template>
      </el-table-column>
      <el-table-column prop="submitted_at" label="提交时间" width="165">
        <template #default="{ row }">{{ (row.submitted_at || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openDetail(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-dialog v-model="detailVisible" :title="`毕业材料 - ${current?.student_name}`" width="700px" top="6vh">
      <div style="max-height: 60vh; overflow-y: auto">
        <el-table :data="current?.files || []" border size="small" style="margin-bottom: 12px">
          <el-table-column prop="material_type_cn" label="类型" width="100" />
          <el-table-column prop="original_name" label="文件名" min-width="200" show-overflow-tooltip />
          <el-table-column label="大小" width="90">
            <template #default="{ row }">{{ (row.size / 1024 / 1024).toFixed(2) }}MB</template>
          </el-table-column>
          <el-table-column label="下载" width="80">
            <template #default="{ row }">
              <el-link :href="row.url" target="_blank" type="primary">下载</el-link>
            </template>
          </el-table-column>
        </el-table>
        <el-divider>审核历史</el-divider>
        <ReviewHistory :records="current?.review_history || []" />
      </div>
      <div style="border-top: 1px solid #ebeef5; padding-top: 12px">
        <el-input v-model="comment" type="textarea" :rows="2" placeholder="审核意见" style="margin-bottom: 8px" />
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <el-button type="danger" plain :loading="reviewing" @click="review(false)">驳回</el-button>
          <el-button type="success" :loading="reviewing" @click="review(true)">通过</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>
