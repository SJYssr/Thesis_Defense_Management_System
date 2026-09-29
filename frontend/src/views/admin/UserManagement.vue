<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { userAdminApi } from '@/api/common'
import AdminInfoPanel from './AdminInfoPanel.vue'

const route = useRoute()
/* 设计稿将管理员/教师/学生信息管理拆为独立页面：ADMIN 用新版面板，其余暂用旧 Tab 版 */
const isAdminView = computed(() => String(route.query.role || '').toUpperCase() === 'ADMIN')
const activeTab = ref('STUDENT')
const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, keyword: '' })

const editing = ref(false)
const saving = ref(false)
const form = reactive<Record<string, any>>({})

const ROLE_TABS = [
  { value: 'STUDENT', label: '学生管理' },
  { value: 'TEACHER', label: '教师管理' },
  { value: 'ADMIN', label: '管理员管理' },
]

async function load() {
  const res = await userAdminApi.list(activeTab.value, {
    page: query.page, page_size: query.page_size, keyword: query.keyword || undefined,
  })
  items.value = res.data.items
  total.value = res.data.total
}

function openCreate() {
  Object.assign(form, {
    id: 0, username: '', password: '123456', real_name: '', role: activeTab.value,
    phone: '', email: '', teacher_no: '', title: '', department: '', research_direction: '',
    max_students: 8, student_no: '', grade: '2023',
  })
  editing.value = true
}

function openEdit(row: any) {
  Object.assign(form, { ...row, password: '' })
  editing.value = true
}

async function save() {
  saving.value = true
  try {
    if (form.id) {
      const { password, ...rest } = form
      await userAdminApi.update(activeTab.value, form.id, password ? { ...rest, password } : rest)
    } else {
      await userAdminApi.create(activeTab.value, form)
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

async function remove(row: any) {
  await ElMessageBox.confirm(`确定删除用户「${row.real_name}」?`, '删除', { type: 'error' })
  await userAdminApi.remove(activeTab.value, row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(() => {
  // 支持侧栏子菜单通过 /admin/users?role=TEACHER 直达对应标签
  const role = String(route.query.role || '').toUpperCase()
  if (['STUDENT', 'TEACHER', 'ADMIN'].includes(role)) activeTab.value = role
  if (!isAdminView.value) load()
})

/* 顶栏面包屑随角色视图切换 */
watchEffect(() => {
  const role = String(route.query.role || '').toUpperCase()
  route.meta.title = role === 'ADMIN' ? '毕业设计管理 / 管理员信息管理'
    : role === 'TEACHER' ? '毕业设计管理 / 教师信息管理'
    : '毕业设计管理 / 学生信息管理'
})

watch(isAdminView, () => {
  if (!isAdminView.value) load()
})
</script>

<template>
  <AdminInfoPanel v-if="isAdminView" />
  <div v-else class="page-card">
    <div class="page-header">
      <h3 class="page-title">用户管理</h3>
      <el-button type="primary" @click="openCreate">新增{{ activeTab === 'STUDENT' ? '学生' : activeTab === 'TEACHER' ? '教师' : '管理员' }}</el-button>
    </div>

    <el-tabs v-model="activeTab" @tab-change="query.page = 1; load()">
      <el-tab-pane v-for="t in ROLE_TABS" :key="t.value" :label="t.label" :name="t.value" />
    </el-tabs>

    <div class="filter-bar">
      <el-input v-model="query.keyword" placeholder="用户名/姓名" clearable style="width: 200px" @keyup.enter="load" />
      <el-button type="primary" @click="load">查询</el-button>
    </div>

    <el-table :data="items" border>
      <el-table-column prop="username" label="登录名" width="120" />
      <el-table-column prop="real_name" label="姓名" width="100" />
      <el-table-column v-if="activeTab === 'TEACHER'" prop="teacher_no" label="工号" width="100" />
      <el-table-column v-if="activeTab === 'STUDENT'" prop="student_no" label="学号" width="110" />
      <el-table-column v-if="activeTab === 'TEACHER'" prop="title" label="职称" width="100" />
      <el-table-column v-if="activeTab === 'TEACHER'" prop="max_students" label="名额上限" width="90" />
      <el-table-column v-if="activeTab === 'STUDENT'" prop="grade" label="年级" width="80" />
      <el-table-column prop="phone" label="手机号" width="130" />
      <el-table-column prop="email" label="邮箱" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'danger'" size="small">{{ row.status === 1 ? '启用' : '禁用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_login_time" label="最后登录" width="165">
        <template #default="{ row }">{{ (row.last_login_time || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>

    <el-dialog v-model="editing" :title="form.id ? '编辑用户' : '新增用户'" width="600px">
      <el-form label-width="90px">
        <el-row>
          <el-col :span="12">
            <el-form-item label="登录名" required>
              <el-input v-model="form.username" :disabled="!!form.id" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="姓名" required>
              <el-input v-model="form.real_name" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="密码">
          <el-input v-model="form.password" :placeholder="form.id ? '留空则不修改' : '初始密码'" />
        </el-form-item>
        <el-row v-if="activeTab === 'TEACHER'">
          <el-col :span="12">
            <el-form-item label="工号" required>
              <el-input v-model="form.teacher_no" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="职称">
              <el-input v-model="form.title" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row v-if="activeTab === 'TEACHER'">
          <el-col :span="12">
            <el-form-item label="研究方向">
              <el-input v-model="form.research_direction" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名额上限">
              <el-input-number v-model="form.max_students" :min="1" :max="20" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row v-if="activeTab === 'STUDENT'">
          <el-col :span="12">
            <el-form-item label="学号" required>
              <el-input v-model="form.student_no" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="年级">
              <el-input v-model="form.grade" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row>
          <el-col :span="12">
            <el-form-item label="手机号">
              <el-input v-model="form.phone" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="邮箱">
              <el-input v-model="form.email" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editing = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
