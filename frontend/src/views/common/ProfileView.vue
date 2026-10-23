<template>
  <div class="profile-page">
    <!-- 左栏：个人信息卡 + 页签卡 -->
    <div class="side-col">
      <div class="profile-card">
        <div class="avatar">
          <svg viewBox="0 0 96 96">
            <circle cx="48" cy="48" r="48" fill="#d7f0fe" />
            <ellipse cx="48" cy="80" rx="30" ry="24" fill="#1d3557" />
            <path d="M38 62l10 14 10-14-10-5z" fill="#fff" />
            <path d="M45 66l3 10 3-10-3-3z" fill="#2e97fe" />
            <ellipse cx="48" cy="40" rx="15" ry="17" fill="#f2c9a8" />
            <path d="M33 36c0-9 7-14 15-14s15 5 15 14l-2-2c-1 4-4 4-7 3-5-2-12-2-16 1-2 1-3 2-5-2z" fill="#2c3e5d" />
            <circle cx="42" cy="41" r="1.6" fill="#2c3e5d" />
            <circle cx="54" cy="41" r="1.6" fill="#2c3e5d" />
            <path d="M44 48c2 2 6 2 8 0" stroke="#c98850" stroke-width="1.4" fill="none" stroke-linecap="round" />
          </svg>
        </div>
        <div class="p-name">{{ displayName }}</div>
        <div class="p-role">{{ roleCn }}</div>
        <div class="p-status"><i />在线</div>
      </div>

      <div class="nav-card">
        <div class="nav-item" :class="{ active: tab === 'info' }" @click="switchTab('info')">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0 2c-4.4 0-8 2.2-8 5v3h16v-3c0-2.8-3.6-5-8-5z"/></svg>
          <span>个人信息</span>
        </div>
        <div class="nav-item" :class="{ active: tab === 'password' }" @click="switchTab('password')">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M17 9V7a5 5 0 0 0-10 0v2H5v13h14V9h-2zM9 7a3 3 0 0 1 6 0v2H9V7zm4 9.7V19h-2v-2.3a2 2 0 1 1 2 0z"/></svg>
          <span>修改密码</span>
        </div>
      </div>
    </div>

    <!-- 右栏：内容卡 -->
    <div class="main-col">
      <!-- 修改密码 -->
      <div v-if="tab === 'password'" class="panel-card">
        <div class="panel-head">
          <div class="lock-badge">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M17 9V7a5 5 0 0 0-10 0v2H5v13h14V9h-2zM9 7a3 3 0 0 1 6 0v2H9V7zm4 9.7V19h-2v-2.3a2 2 0 1 1 2 0z"/></svg>
          </div>
          <div>
            <div class="panel-title">修改密码</div>
            <div class="panel-sub">为了您的账户安全，请定期修改密码</div>
          </div>
        </div>

        <el-alert
          v-if="mustChangePassword"
          type="warning"
          :closable="false"
          show-icon
          style="margin: 0 0 14px"
          title="当前账号仍在使用初始密码"
          description="为保障账号安全，请立即修改密码后再使用系统功能。"
        />

        <el-form label-position="left" label-width="96px" class="pwd-form">
          <el-form-item required>
            <template #label>原密码</template>
            <el-input v-model="pwdForm.old_password" type="password" suffix-icon="View" placeholder="请输入原密码" />
          </el-form-item>
          <el-form-item required>
            <template #label>新密码</template>
            <el-input v-model="pwdForm.new_password" type="password" suffix-icon="View" placeholder="请输入新密码" />
            <div class="pwd-tip">
              <svg viewBox="0 0 24 24" fill="none" stroke="#1878fd" stroke-width="1.6"><circle cx="12" cy="12" r="9" /><path d="M12 11v5" stroke-linecap="round" /><circle cx="12" cy="8" r="0.8" fill="#1878fd" stroke="none" /></svg>
              <span>密码长度8-20位，包含字母、数字和特殊字符中的至少两种</span>
            </div>
          </el-form-item>
          <el-form-item required>
            <template #label>确认新密码</template>
            <el-input v-model="pwdForm.confirm" type="password" suffix-icon="View" placeholder="请再次输入新密码" />
          </el-form-item>
        </el-form>

        <div class="panel-actions">
          <el-button class="ghost-btn" @click="resetPwd">取 消</el-button>
          <el-button type="primary" :loading="changing" @click="changePwd">确定修改</el-button>
        </div>
      </div>

      <!-- 个人信息 -->
      <div v-else class="panel-card">
        <div class="panel-head">
          <div class="lock-badge">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0 2c-4.4 0-8 2.2-8 5v3h16v-3c0-2.8-3.6-5-8-5z"/></svg>
          </div>
          <div>
            <div class="panel-title">个人信息</div>
            <div class="panel-sub">维护您的基本资料与联系方式</div>
          </div>
        </div>

        <el-form label-position="left" label-width="96px" class="pwd-form">
          <el-form-item label="登录名">
            <el-input :model-value="profile?.username || '-'" disabled />
          </el-form-item>
          <el-form-item required>
            <template #label>姓名</template>
            <el-input v-model="form.real_name" placeholder="请输入姓名" />
          </el-form-item>
          <el-form-item>
            <template #label>手机号</template>
            <el-input v-model="form.phone" placeholder="请输入手机号" />
          </el-form-item>
          <el-form-item>
            <template #label>邮箱</template>
            <el-input v-model="form.email" placeholder="请输入邮箱" />
          </el-form-item>
        </el-form>

        <div class="panel-actions">
          <el-button class="ghost-btn" @click="load">重 置</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存修改</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api/auth'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** 默认展示"个人信息"; 只有显式 tab=password 才切到改密页(登录页强制改密会用这个参数) */
const tab = computed(() => (route.query.tab === 'password' ? 'password' : 'info'))
/** P1-B: must_change_password 来自 /auth/me(profile)，只提示不强制拦截 */
const mustChangePassword = computed(() => userStore.mustChangePassword)

const ROLE_CN: Record<string, string> = { ADMIN: '系统管理员', TEACHER: '教师', STUDENT: '学生' }
const roleCn = computed(() => ROLE_CN[userStore.role] || userStore.role || '系统管理员')
const displayName = computed(() => (userStore.role === 'ADMIN' ? '管理员' : userStore.user?.real_name) || '管理员')

function switchTab(t: string) {
  router.replace({ query: { ...route.query, tab: t } })
}

const profile = ref<any>(null)
const form = reactive({ real_name: '', phone: '', email: '' })
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })
const saving = ref(false)
const changing = ref(false)

async function load() {
  const res = await authApi.getProfile()
  profile.value = res.data
  Object.assign(form, { real_name: res.data.real_name, phone: res.data.phone || '', email: res.data.email || '' })
}

async function save() {
  if (!form.real_name.trim()) {
    ElMessage.warning('请输入姓名')
    return
  }
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

function resetPwd() {
  Object.assign(pwdForm, { old_password: '', new_password: '', confirm: '' })
}

async function changePwd() {
  if (!pwdForm.old_password) {
    ElMessage.warning('请输入原密码')
    return
  }
  if (pwdForm.new_password.length < 8 || pwdForm.new_password.length > 20) {
    ElMessage.warning('密码长度8-20位')
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

onMounted(load)
</script>

<style scoped>
.profile-page {
  display: flex;
  gap: 14px;
  align-items: stretch;
  min-height: calc(100vh - 96px);
}

/* 左栏 */
.side-col {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.profile-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  padding: 30px 20px 24px;
  text-align: center;
}
.avatar {
  width: 84px;
  height: 84px;
  margin: 0 auto;
}
.avatar svg {
  width: 100%;
  height: 100%;
}
.p-name {
  margin-top: 12px;
  font-size: 18px;
  font-weight: 700;
  color: #1f2d3d;
}
.p-role {
  margin-top: 4px;
  font-size: 13px;
  color: #8a97ad;
}
.p-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 3px 14px;
  border-radius: 12px;
  background: #e4f7ec;
  color: #27ae60;
  font-size: 12px;
}
.p-status i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #27ae60;
}

.nav-card {
  flex: 1;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  padding: 8px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 14px;
  border-radius: 6px;
  font-size: 14px;
  color: #3b4a5f;
  cursor: pointer;
}
.nav-item svg {
  width: 17px;
  height: 17px;
}
.nav-item.active {
  background: #e8f1fe;
  color: #1878fd;
  font-weight: 500;
}

/* 右栏 */
.main-col {
  flex: 1;
  min-width: 0;
}
.panel-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  padding: 24px 32px;
  height: 100%;
  box-sizing: border-box;
}
.panel-head {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-bottom: 18px;
  border-bottom: 1px solid #eef2f8;
  margin-bottom: 26px;
}
.lock-badge {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #1878fd;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.lock-badge svg {
  width: 22px;
  height: 22px;
  color: #fff;
}
.panel-title {
  font-size: 17px;
  font-weight: 700;
  color: #1f2d3d;
}
.panel-sub {
  margin-top: 3px;
  font-size: 12.5px;
  color: #8a97ad;
}

.pwd-form {
  max-width: none;
}
.pwd-form :deep(.el-form-item__label) {
  font-size: 13.5px;
  color: #3b4a5f;
}
/* 宽度必须自适应: 固定 560px + 96px 标签 + 32px*2 内边距 = 720px 最小需求,
   容器不足时不收缩就会顶出卡片 */
.pwd-form :deep(.el-input) {
  width: 100%;
  max-width: 560px;
}
.pwd-form :deep(.el-form-item__content) {
  flex: 1 1 auto;
  min-width: 0;
  flex-wrap: wrap;
}
.pwd-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 560px;
  max-width: 560px;
  min-width: 0;
  box-sizing: border-box;
  margin-top: 8px;
  padding: 7px 12px;
  border-radius: 6px;
  background: #e8f1fe;
  font-size: 12px;
  color: #5a6b80;
}
.pwd-tip svg {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
}
.pwd-tip span {
  white-space: normal;
  line-height: 1.5;
}

.panel-actions {
  display: flex;
  gap: 12px;
  margin-top: 28px;
  padding-left: 96px;
}
.ghost-btn {
  border: 1px solid #dcdfe6;
  color: #3b4a5f;
}

@media (max-width: 1200px) {
  .side-col {
    width: 260px;
  }
}
@media (max-width: 1024px) {
  .profile-page {
    flex-direction: column;
    min-height: 0;
  }
  .side-col {
    width: 100%;
    flex-direction: row;
    align-items: stretch;
  }
  .profile-card {
    flex-shrink: 0;
  }
  .nav-card {
    flex: 1;
  }
  .panel-card {
    height: auto;
  }
  /* 标签上置: 窄屏保留 96px 左标签会把输入框压成一条 */
  .pwd-form :deep(.el-form-item) {
    flex-direction: column;
    align-items: stretch;
  }
  .pwd-form :deep(.el-form-item__label) {
    width: auto !important;
    padding: 0 0 6px;
    text-align: left;
    line-height: 1.4;
  }
  .pwd-form :deep(.el-input) {
    max-width: none;
  }
  .pwd-tip {
    flex: 1 1 100%;
    max-width: none;
  }
  .panel-actions {
    padding-left: 0;
  }
}
</style>
