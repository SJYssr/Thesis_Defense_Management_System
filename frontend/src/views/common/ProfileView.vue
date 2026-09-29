<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api/auth'
import { useUserStore } from '@/stores/user'
import { announcementApi } from '@/api/common'

const userStore = useUserStore()
const profile = ref<any>(null)
const form = reactive({ real_name: '', phone: '', email: '' })
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })
const saving = ref(false)
const changing = ref(false)

const ROLE_CN: Record<string, string> = { ADMIN: '管理员', TEACHER: '教师', STUDENT: '学生' }

async function load() {
  const res = await authApi.getProfile()
  profile.value = res.data
  Object.assign(form, { real_name: res.data.real_name, phone: res.data.phone || '', email: res.data.email || '' })
}

async function save() {
  saving.value = true
  try {
    await authApi.updateProfile(form)
    await userStore.refreshProfile()
    ElMessage.success('资料已更新')
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

async function changePwd() {
  if (pwdForm.new_password.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  if (pwdForm.new_password !== pwdForm.confirm) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  changing.value = true
  try {
    await authApi.changePassword({ old_password: pwdForm.old_password, new_password: pwdForm.new_password })
    ElMessage.success('密码已修改, 请重新登录')
    userStore.logout()
    location.href = '/login'
  } catch {
    /* 已提示 */
  } finally {
    changing.value = false
  }
}

// 公告详情抽屉
const announcements = ref<any[]>([])
const detailVisible = ref(false)
const currentAnn = ref<any>(null)

function viewAnn(a: any) {
  currentAnn.value = a
  detailVisible.value = true
}

onMounted(async () => {
  await load()
  const res = await announcementApi.list({ page: 1, page_size: 20 })
  announcements.value = res.data.items
})
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="8">
      <div class="page-card" style="text-align: center; padding: 32px">
        <el-avatar :size="80" style="background: #409eff; font-size: 32px">
          {{ (profile?.real_name || '?').slice(0, 1) }}
        </el-avatar>
        <h3 style="margin: 12px 0 4px">{{ profile?.real_name }}</h3>
        <el-tag>{{ ROLE_CN[profile?.role] || profile?.role }}</el-tag>
        <div style="color: #909399; font-size: 13px; margin-top: 12px">
          登录名: {{ profile?.username }}
          <template v-if="profile?.student_no"> · 学号: {{ profile.student_no }}</template>
          <template v-if="profile?.teacher_no"> · 工号: {{ profile.teacher_no }}</template>
        </div>
      </div>
    </el-col>

    <el-col :span="16">
      <div class="page-card">
        <h3 style="margin: 0 0 16px">个人资料</h3>
        <el-form label-width="80px" style="max-width: 420px">
          <el-form-item label="姓名">
            <el-input v-model="form.real_name" />
          </el-form-item>
          <el-form-item label="手机号">
            <el-input v-model="form.phone" />
          </el-form-item>
          <el-form-item label="邮箱">
            <el-input v-model="form.email" />
          </el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </el-form>

        <el-divider>修改密码</el-divider>
        <el-form label-width="80px" style="max-width: 420px">
          <el-form-item label="原密码">
            <el-input v-model="pwdForm.old_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input v-model="pwdForm.new_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="确认密码">
            <el-input v-model="pwdForm.confirm" type="password" show-password />
          </el-form-item>
          <el-button type="warning" :loading="changing" @click="changePwd">修改密码</el-button>
        </el-form>
      </div>
    </el-col>

    <el-col :span="24" style="margin-top: 16px">
      <div class="page-card">
        <h3 style="margin: 0 0 12px">系统公告</h3>
        <el-table :data="announcements" border @row-click="viewAnn" style="cursor: pointer">
          <el-table-column prop="title" label="标题" min-width="260" />
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
      </div>
    </el-col>

    <el-drawer v-model="detailVisible" :title="currentAnn?.title" size="40%">
      <div style="padding: 0 8px">
        <div style="color: #909399; font-size: 13px; margin-bottom: 12px">
          发布于 {{ (currentAnn?.publish_time || '').replace('T', ' ').slice(0, 16) }}
        </div>
        <div style="white-space: pre-wrap; line-height: 1.8; color: #303133">{{ currentAnn?.content }}</div>
      </div>
    </el-drawer>
  </el-row>
</template>
