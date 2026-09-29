<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { authApi } from '@/api/auth'
import { useUserStore } from '@/stores/user'
import { MEDIA } from '@/config/media'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const form = reactive({ username: '', password: '' })
const remember = ref(true)
const showPwd = ref(false)
const activeRole = ref('admin')

const loginBg = MEDIA.loginBg

/* ===== 图标 ===== */
const CAP_ICON = '<path d="M12 3 1.5 8.2 12 13.4l8.6-4.6v5.8h2V8.2L12 3zm-6.5 9v4.2c0 1.5 2.9 3 6.5 3s6.5-1.5 6.5-3V12l-6.5 3.5L5.5 12z"/>'
const USER_ICON = '<path d="M12 12a4.2 4.2 0 1 0 0-8.4A4.2 4.2 0 0 0 12 12zm0 2.1c-4.4 0-8 2.2-8 5V21h16v-1.9c0-2.8-3.6-5-8-5z"/>'
const LOCK_ICON = '<path d="M12 2.8A4.4 4.4 0 0 0 7.6 7.2V10H6.8A1.8 1.8 0 0 0 5 11.8v7.4A1.8 1.8 0 0 0 6.8 21h10.4a1.8 1.8 0 0 0 1.8-1.8v-7.4A1.8 1.8 0 0 0 17.2 10h-.8V7.2A4.4 4.4 0 0 0 12 2.8zm0 2a2.4 2.4 0 0 1 2.4 2.4V10H9.6V7.2A2.4 2.4 0 0 1 12 4.8z"/>'
const EYE_ICON = '<path d="M12 5c-4.5 0-8.3 2.9-9.8 7 1.5 4.1 5.3 7 9.8 7s8.3-2.9 9.8-7c-1.5-4.1-5.3-7-9.8-7zm0 11.6a4.6 4.6 0 1 1 0-9.2 4.6 4.6 0 0 1 0 9.2zm0-7.4a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z"/>'
const EYE_OFF_ICON = '<path d="M2.1 3.5 3.5 2.1l18.4 18.4-1.4 1.4-3.3-3.3A11.4 11.4 0 0 1 12 19c-4.5 0-8.3-2.9-9.8-7a11.7 11.7 0 0 1 3.5-5L2.1 3.5zM12 5c1.2 0 2.4.2 3.5.6a5.8 5.8 0 0 1-7.6 7.9L6 11.5A9.7 9.7 0 0 1 12 5zm5.8 3.9 2.2-2.2A11.6 11.6 0 0 1 21.8 12c-1.5 4.1-5.3 7-9.8 7-1 0-2-.1-2.9-.4l2.4-2.4a5.8 5.8 0 0 0 5.3-7.7z"/>'
const WECHAT_ICON = '<path d="M9.3 4C5.3 4 2 6.8 2 10.2c0 1.9 1 3.6 2.6 4.7l-.7 2 2.3-1.2c.7.2 1.4.4 2.2.4h.4a5.4 5.4 0 0 1-.2-1.5c0-3.2 3-5.8 6.8-5.8h.4C15.1 6 12.5 4 9.3 4zM6.9 7.4a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8zm4.8 0a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8zM16 10.2c-3.2 0-5.8 2.1-5.8 4.8 0 2.6 2.6 4.8 5.8 4.8.6 0 1.2-.1 1.8-.3l2 1-.6-1.7c1.4-.9 2.4-2.3 2.4-3.8 0-2.7-2.5-4.8-5.6-4.8zm-2 2.5a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6zm4 0a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6z"/>'
const BUILDING_ICON = '<path d="M12 2 2.5 7v2h19V7L12 2zM4.5 10.5h3V18h-3v-7.5zm6 0h3V18h-3v-7.5zm6 0h3V18h-3v-7.5zM3 19.5h18V22H3v-2.5z"/>'

const ROLES = [
  { key: 'admin', label: '管理员', username: 'admin', password: 'admin123' },
  { key: 'teacher', label: '教师', username: 'teacher01', password: '123456' },
  { key: 'student', label: '学生', username: '20230001', password: '123456' },
]

onMounted(() => {
  const saved = localStorage.getItem('remember_username')
  if (saved) {
    form.username = saved
    remember.value = true
  }
})

function switchRole(key: string) {
  activeRole.value = key
  const role = ROLES.find((r) => r.key === key)
  if (role) {
    form.username = role.username
    form.password = role.password
  }
}

function forgot() {
  ElMessage.info('请联系管理员重置密码')
}

function thirdParty(name: string) {
  ElMessage.info(`${name}暂未开通`)
}

async function handleLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const res = await authApi.login(form)
    if (remember.value) {
      localStorage.setItem('remember_username', form.username)
    } else {
      localStorage.removeItem('remember_username')
    }
    userStore.setSession(res.data)
    ElMessage.success(`欢迎回来, ${res.data.user.real_name}`)
    const map: Record<string, string> = { ADMIN: '/admin/home', TEACHER: '/teacher/home', STUDENT: '/student/home' }
    router.push(map[res.data.user.role] || '/login')
  } catch {
    /* 错误提示由拦截器统一处理 */
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page" :style="{ backgroundImage: `url(${loginBg})` }">
    <!-- ===== 顶部栏 ===== -->
    <header class="topbar">
      <div class="brand">
        <i class="brand-cap"><svg viewBox="0 0 24 24" fill="currentColor" v-html="CAP_ICON" /></i>
        <span>毕业设计管理系统</span>
      </div>
      <nav class="top-links">
        <a @click="ElMessage.info('帮助中心建设中')">帮助中心</a>
        <span class="sep" />
        <a @click="ElMessage.info('使用手册建设中')">使用手册</a>
        <span class="sep" />
        <a @click="ElMessage.info('联系方式：admin@school.edu.cn')">联系我们</a>
      </nav>
    </header>

    <!-- ===== 主体 ===== -->
    <main class="login-main">
      <!-- 右侧登录卡片 -->
      <section class="login-card">
        <div class="card-brand">
          <i class="card-cap"><svg viewBox="0 0 24 24" fill="currentColor" v-html="CAP_ICON" /></i>
          <div>
            <div class="card-title">毕业设计管理系统</div>
            <div class="card-sub">欢迎登录系统</div>
          </div>
        </div>

        <div class="role-tabs">
          <div
            v-for="r in ROLES" :key="r.key"
            class="role-tab" :class="{ active: activeRole === r.key }"
            @click="switchRole(r.key)"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" v-html="USER_ICON" />
            <span>{{ r.label }}</span>
          </div>
        </div>

        <div class="field">
          <i class="prefix"><svg viewBox="0 0 24 24" fill="currentColor" v-html="USER_ICON" /></i>
          <input v-model="form.username" type="text" placeholder="请输入账号" autocomplete="username" @keyup.enter="handleLogin" />
        </div>
        <div class="field">
          <i class="prefix"><svg viewBox="0 0 24 24" fill="currentColor" v-html="LOCK_ICON" /></i>
          <input
            v-model="form.password" :type="showPwd ? 'text' : 'password'"
            placeholder="请输入密码" autocomplete="current-password" @keyup.enter="handleLogin"
          />
          <i class="suffix" @click="showPwd = !showPwd">
            <svg viewBox="0 0 24 24" fill="currentColor" v-html="showPwd ? EYE_ICON : EYE_OFF_ICON" />
          </i>
        </div>

        <div class="mid-row">
          <el-checkbox v-model="remember" class="remember">记住我</el-checkbox>
          <a class="forgot" @click="forgot">忘记密码?</a>
        </div>

        <button class="login-btn" :disabled="loading" @click="handleLogin">
          <span v-if="!loading">登 录</span>
          <span v-else>登录中…</span>
        </button>

        <div class="divider"><span>其他登录方式</span></div>

        <div class="third-row">
          <div class="third-item" @click="thirdParty('微信')">
            <i class="third-ic wechat"><svg viewBox="0 0 24 24" fill="currentColor" v-html="WECHAT_ICON" /></i>
            <span>微信登录</span>
          </div>
          <div class="third-item" @click="thirdParty('统一身份认证')">
            <i class="third-ic sso"><svg viewBox="0 0 24 24" fill="currentColor" v-html="BUILDING_ICON" /></i>
            <span>统一身份认证</span>
          </div>
        </div>
      </section>
    </main>

    <!-- ===== 底部 ===== -->
    <footer class="footer">powered by sjyssr</footer>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #eaf3fe no-repeat center / cover;
  position: relative;
}

/* ===== 顶部栏 ===== */
.topbar {
  height: 74px;
  padding: 0 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 19px;
  font-weight: 700;
  color: #17293f;
}
.brand-cap {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #1878fd;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.brand-cap svg { width: 20px; height: 20px; }
.top-links {
  display: flex;
  align-items: center;
  gap: 22px;
  font-size: 14px;
  color: #2c3e5d;
}
.top-links a { cursor: pointer; }
.top-links a:hover { color: #1878fd; }
.top-links .sep { width: 1px; height: 14px; background: #b9c9de; }

/* ===== 主体 ===== */
.login-main {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 20px 7% 30px 56px;
}

/* ===== 登录卡片 ===== */
.login-card {
  width: 470px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 18px 50px rgba(23, 61, 110, 0.16);
  padding: 38px 46px 34px;
}
.card-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.card-cap {
  width: 46px;
  height: 46px;
  color: #1878fd;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.card-cap svg { width: 44px; height: 44px; }
.card-title {
  font-size: 21px;
  font-weight: 800;
  color: #17293f;
}
.card-sub {
  margin-top: 3px;
  font-size: 12.5px;
  color: #97a3b4;
  text-align: center;
}
.card-brand > div { text-align: center; }

/* 角色标签 */
.role-tabs {
  margin-top: 26px;
  display: flex;
  border-bottom: 1px solid #e6ecf4;
}
.role-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 11px 0;
  font-size: 15px;
  color: #6b7f99;
  cursor: pointer;
  position: relative;
}
.role-tab svg { width: 17px; height: 17px; }
.role-tab.active { color: #1878fd; font-weight: 600; }
.role-tab.active::after {
  content: '';
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: -1px;
  width: 56px;
  height: 3px;
  border-radius: 2px;
  background: #1878fd;
}

/* 输入框 */
.field {
  margin-top: 20px;
  display: flex;
  align-items: center;
  border: 1px solid #dbe3ee;
  border-radius: 10px;
  background: #fff;
  padding: 0 14px;
  height: 48px;
  transition: border-color 0.15s;
}
.field:focus-within { border-color: #1878fd; }
.prefix, .suffix {
  display: inline-flex;
  color: #9aa9bd;
  flex-shrink: 0;
}
.prefix svg, .suffix svg { width: 17px; height: 17px; }
.suffix { cursor: pointer; }
.suffix:hover { color: #1878fd; }
.field input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  height: 100%;
  margin-left: 10px;
  font-size: 14px;
  color: #1f2d3d;
}
.field input::placeholder { color: #a8b3c5; }

.mid-row {
  margin-top: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.remember :deep(.el-checkbox__inner) {
  width: 17px;
  height: 17px;
  border-radius: 4px;
}
.remember :deep(.el-checkbox__label) { font-size: 14px; color: #3b4a5f; }
.forgot { font-size: 13.5px; color: #1878fd; cursor: pointer; }
.forgot:hover { text-decoration: underline; }

.login-btn {
  margin-top: 18px;
  width: 100%;
  height: 48px;
  border: none;
  border-radius: 10px;
  background: #1878fd;
  color: #fff;
  font-size: 16.5px;
  letter-spacing: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.login-btn:hover { background: #166ae2; }
.login-btn:disabled { opacity: 0.72; cursor: default; }

/* 其他登录方式 */
.divider {
  margin-top: 26px;
  display: flex;
  align-items: center;
  gap: 14px;
  color: #9aa9bd;
  font-size: 12.5px;
}
.divider::before, .divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: #e6ecf4;
}
.third-row {
  margin-top: 18px;
  display: flex;
  justify-content: center;
  gap: 56px;
}
.third-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: #6b7f99;
  cursor: pointer;
}
.third-item:hover { color: #1878fd; }
.third-ic {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}
.third-ic svg { width: 22px; height: 22px; }
.third-ic.wechat { background: #2aae67; }
.third-ic.sso { background: #1878fd; }

/* ===== 底部 ===== */
.footer {
  flex-shrink: 0;
  text-align: center;
  padding: 14px 0 16px;
  font-size: 12.5px;
  color: rgba(23, 41, 63, 0.55);
}

/* 窄屏简单适配 */
@media (max-width: 900px) {
  .login-main { justify-content: center; padding: 20px 16px; }
  .login-card { width: 100%; max-width: 470px; padding: 30px 24px; }
}
</style>
