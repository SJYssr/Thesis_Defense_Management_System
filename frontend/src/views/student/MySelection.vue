<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { selectionApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'

const items = ref<any[]>([])

async function load() {
  const res = await selectionApi.mine()
  items.value = res.data
}

async function cancel(row: any) {
  await ElMessageBox.confirm('确定撤销该选题申请吗?', '提示', { type: 'warning' })
  await selectionApi.cancel(row.id)
  ElMessage.success('已撤销')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">我的选题</h3>
    </div>
    <el-table :data="items" border>
      <el-table-column prop="topic_title" label="题目名称" min-width="240" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <StatusTag :status="row.status" />
        </template>
      </el-table-column>
      <el-table-column prop="student_reason" label="选题理由" min-width="200" show-overflow-tooltip />
      <el-table-column prop="review_comment" label="审核意见" min-width="160" show-overflow-tooltip />
      <el-table-column prop="review_time" label="审核时间" width="170">
        <template #default="{ row }">{{ (row.review_time || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'PENDING'" size="small" type="danger" plain @click="cancel(row)">撤销</el-button>
          <span v-else style="color: #c0c4cc; font-size: 12px">{{ row.status === 'APPROVED' ? '生效中' : '已归档' }}</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!items.length" description="尚未申请任何题目, 请前往题目中心选题" />
  </div>
</template>
