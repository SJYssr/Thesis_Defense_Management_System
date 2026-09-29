<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { announcementApi } from '@/api/common'

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20 })

const editing = ref(false)
const saving = ref(false)
const form = reactive({ id: 0, title: '', content: '', priority: 'NORMAL' })

async function load() {
  const res = await announcementApi.adminList(query)
  items.value = res.data.items
  total.value = res.data.total
}

function openCreate() {
  Object.assign(form, { id: 0, title: '', content: '', priority: 'NORMAL' })
  editing.value = true
}

function openEdit(row: any) {
  Object.assign(form, { id: row.id, title: row.title, content: row.content, priority: row.priority })
  editing.value = true
}

async function save() {
  saving.value = true
  try {
    if (form.id) await announcementApi.update(form.id, form)
    else await announcementApi.create(form)
    ElMessage.success('保存成功')
    editing.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

async function remove(row: any) {
  await ElMessageBox.confirm(`下架公告「${row.title}」?`, '下架', { type: 'warning' })
  await announcementApi.remove(row.id)
  ElMessage.success('已下架')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">公告管理</h3>
      <el-button type="primary" @click="openCreate">发布公告</el-button>
    </div>
    <el-table :data="items" border>
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column label="优先级" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.priority === 'URGENT' ? 'danger' : row.priority === 'IMPORTANT' ? 'warning' : 'info'">
            {{ row.priority_cn }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'PUBLISHED' ? 'success' : 'info'">
            {{ row.status === 'PUBLISHED' ? '已发布' : '已下线' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="publish_time" label="发布时间" width="165">
        <template #default="{ row }">{{ (row.publish_time || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.status === 'PUBLISHED'" size="small" type="warning" plain @click="remove(row)">下架</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-dialog v-model="editing" :title="form.id ? '编辑公告' : '发布公告'" width="600px">
      <el-form label-width="80px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" maxlength="255" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-radio-group v-model="form.priority">
            <el-radio value="NORMAL">普通</el-radio>
            <el-radio value="IMPORTANT">重要</el-radio>
            <el-radio value="URGENT">紧急</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="内容" required>
          <el-input v-model="form.content" type="textarea" :rows="8" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editing = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">发布</el-button>
      </template>
    </el-dialog>
  </div>
</template>
