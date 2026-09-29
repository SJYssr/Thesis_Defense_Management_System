<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { topicApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, status: '' })
const editing = ref(false)
const saving = ref(false)
const form = reactive({
  id: 0, title: '', description: '', requirements: '', topic_type: '', difficulty: 'MEDIUM', max_students: 1,
})

async function load() {
  const res = await topicApi.teacherList({ ...query, status: query.status || undefined })
  items.value = res.data.items
  total.value = res.data.total
}

function openCreate() {
  Object.assign(form, { id: 0, title: '', description: '', requirements: '', topic_type: '', difficulty: 'MEDIUM', max_students: 1 })
  editing.value = true
}

function openEdit(row: any) {
  Object.assign(form, { id: row.id, title: row.title, description: row.description, requirements: row.requirements,
                        topic_type: row.topic_type, difficulty: row.difficulty || 'MEDIUM', max_students: row.max_students })
  editing.value = true
}

async function save() {
  saving.value = true
  try {
    if (form.id) {
      await topicApi.update(form.id, form)
    } else {
      await topicApi.create(form)
    }
    ElMessage.success('保存成功')
    editing.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

async function publish(row: any) {
  await ElMessageBox.confirm(`发布后学生即可看到「${row.title}」, 确定?`, '发布题目', { type: 'info' })
  await topicApi.publish(row.id)
  ElMessage.success('已发布')
  await load()
}

async function close(row: any) {
  await ElMessageBox.confirm(`下架后学生将无法再选择该题目, 确定?`, '下架题目', { type: 'warning' })
  await topicApi.close(row.id)
  ElMessage.success('已下架')
  await load()
}

async function remove(row: any) {
  await ElMessageBox.confirm(`删除题目「${row.title}」?`, '删除', { type: 'error' })
  await topicApi.remove(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">我的题目</h3>
      <el-button type="primary" @click="openCreate">新增题目</el-button>
    </div>
    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="状态" clearable style="width: 130px" @change="load">
        <el-option label="草稿" value="DRAFT" />
        <el-option label="已发布" value="PUBLISHED" />
        <el-option label="已关闭" value="CLOSED" />
      </el-select>
    </div>
    <el-table :data="items" border>
      <el-table-column prop="title" label="题目名称" min-width="240" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column label="选题人数" width="90" align="center">
        <template #default="{ row }">{{ row.approved_count }}/{{ row.max_students }}</template>
      </el-table-column>
      <el-table-column label="待审申请" width="90" align="center">
        <template #default="{ row }">
          <el-badge v-if="row.pending_count" :value="row.pending_count" type="warning" />
          <span v-else style="color: #c0c4cc">0</span>
        </template>
      </el-table-column>
      <el-table-column prop="publish_time" label="发布时间" width="170">
        <template #default="{ row }">{{ (row.publish_time || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.status === 'DRAFT'" size="small" type="success" @click="publish(row)">发布</el-button>
          <el-button v-if="row.status === 'PUBLISHED'" size="small" type="warning" plain @click="close(row)">下架</el-button>
          <el-button v-if="row.status !== 'PUBLISHED'" size="small" type="danger" plain @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-dialog v-model="editing" :title="form.id ? '编辑题目' : '新增题目'" width="640px">
      <el-form label-width="90px">
        <el-form-item label="题目名称" required>
          <el-input v-model="form.title" maxlength="255" />
        </el-form-item>
        <el-form-item label="题目描述">
          <el-input v-model="form.description" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="题目要求">
          <el-input v-model="form.requirements" type="textarea" :rows="3" />
        </el-form-item>
        <el-row>
          <el-col :span="12">
            <el-form-item label="题目类型">
              <el-input v-model="form.topic_type" placeholder="如: 应用开发/算法研究" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="难度">
              <el-select v-model="form.difficulty">
                <el-option label="简单" value="EASY" />
                <el-option label="中等" value="MEDIUM" />
                <el-option label="困难" value="HARD" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="最大名额">
          <el-input-number v-model="form.max_students" :min="1" :max="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editing = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
