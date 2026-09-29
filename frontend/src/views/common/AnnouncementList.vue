<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { announcementApi } from '@/api/common'

const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const detailVisible = ref(false)
const current = ref<any>(null)

async function load() {
  const res = await announcementApi.list({ page: page.value, page_size: 15 })
  items.value = res.data.items
  total.value = res.data.total
}

function view(a: any) {
  current.value = a
  detailVisible.value = true
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">系统公告</h3>
    </div>
    <el-table :data="items" border @row-click="view" style="cursor: pointer">
      <el-table-column prop="title" label="标题" min-width="300" />
      <el-table-column label="优先级" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.priority === 'URGENT' ? 'danger' : row.priority === 'IMPORTANT' ? 'warning' : 'info'">
            {{ row.priority_cn }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="publish_time" label="发布时间" width="170">
        <template #default="{ row }">{{ (row.publish_time || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="page" :page-size="15" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-drawer v-model="detailVisible" :title="current?.title" size="40%">
      <div style="padding: 0 8px">
        <div style="color: #909399; font-size: 13px; margin-bottom: 12px">
          发布于 {{ (current?.publish_time || '').replace('T', ' ').slice(0, 16) }}
        </div>
        <div style="white-space: pre-wrap; line-height: 1.8; color: #303133">{{ current?.content }}</div>
      </div>
    </el-drawer>
  </div>
</template>
