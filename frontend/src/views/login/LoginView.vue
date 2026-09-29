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
const remember = ref(false)
const activeTab = ref('account')

const loginBg = MEDIA.loginBg
// 设计图裁剪版背景自带文字 → 关闭代码文字层; 换无文字背景图时在 media.ts 打开
const showOverlay = MEDIA.loginShowcaseOverlay

const features = [
  { title: '全过程管理', desc: '选题到毕业全流程', style: { left: '7%', top: '20%' } },
  { title: '多角色协同', desc: '学生 · 导师 · 管理员', style: { right: '7%', top: '14%' } },
  { title: 'AI 智能辅助', desc: '模拟答辩 · 进度分析', style: { left: '10%', top: '33%' } },
  { title: '数据统计分析', desc: '实时同步 · 可视化图表', style: { right: '9%', top: '32%' } },
]

// ---------- 滑块验证 ----------
const trackRef = ref<HTMLElement>()
const sliderVerified = ref(false)
const sliderX = ref(0)
let dragging = false
let dragStartX = 0
const HANDLE = 44

function onHandleDown(e: PointerEvent) {
  if (sliderVerified.value) return
  dragging = true
  dragStartX = e.clientX - sliderX.value
  window.addEventListener('pointermove', onHandleMove)
  window.addEventListener('pointerup', onHandleUp)
}

function onHandleMove(e: PointerEvent) {
  if (!dragging || !trackRef.value) return
  const max = trackRef.value.clientWidth - HANDLE
  sliderX.value = Math.max(0, Math.min(e.clientX - dragStartX, max))
}

function onHandleUp() {
  dragging = false
  window.removeEventListener('pointermove', onHandleMove)
  window.removeEventListener('pointerup', onHandleUp)
  if (!trackRef.value) return
  const max = trackRef.value.clientWidth - HANDLE
  if (sliderX.value >= max - 4) {
    sliderVerified.value = true
  } else {
    sliderX.value = 0 // 未拖到底弹回
  }
}

function resetSlider() {
  sliderVerified.value = false
  sliderX.value = 0
}

// ---------- 登录 ----------
onMounted(() => {
  const saved = localStorage.getItem('remember_username')
  if (saved) {
    form.username = saved
    remember.value = true
  }
})

function switchTab(tab: string) {
  if (tab === 'captcha') {
    ElMessage.info('验证码登录暂未开通，请使用账号登录')
    return
  }
  activeTab.value = tab
}

function forgot() {
  ElMessage.info('请联系管理员重置密码')
}

function thirdParty(name: string) {
  ElMessage.info(`${name}登录暂未开通`)
}

async function handleLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  if (!sliderVerified.value) {
    ElMessage.warning('请先完成滑块验证')
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
    resetSlider()
    const map: Record<string, string> = { ADMIN: '/admin/home', TEACHER: '/teacher/home', STUDENT: '/student/home' }
    router.push(map[res.data.user.role] || '/login')
  } catch {
    resetSlider() // 登录失败需重新验证
  } finally {
    loading.value = false
  }
}

const demos = [
  { label: '管理员', username: 'admin', password: 'admin123' },
  { label: '教师', username: 'teacher01', password: '123456' },
  { label: '学生(含AI演示)', username: '20230001', password: '123456' },
]

function fillDemo(d: { username: string; password: string }) {
  form.username = d.username
  form.password = d.password
  remember.value = true
}
</script>

<template>
  <div class="login-page">
    <!-- 左侧插画区: 背景图可在 public/media/ 中替换 -->
    <div class="showcase" :style="{ backgroundImage: `url(${loginBg})` }">
      <template v-if="showOverlay">
        <div class="brand">
          <h1>毕业设计管理系统</h1>
          <p>规范流程 · 智能辅助 · 高效协同</p>
        </div>
        <div v-for="f in features" :key="f.title" class="feature" :style="f.style">
          <div class="feature-icon"><span class="feature-dot" /></div>
          <div>
            <div class="feature-title">{{ f.title }}</div>
            <div class="feature-desc">{{ f.desc }}</div>
          </div>
        </div>
      </template>
      <div class="showcase-footer">FastAPI · Vue3 · LangChain · RAG</div>
    </div>

    <!-- 右侧登录区(按设计图复刻) -->
    <div class="panel">
      <div class="card">
        <!-- Logo: 浅蓝圆角方块 + 蓝色菱形 + 白色内菱形(三层嵌套) -->
        <div class="logo">
          <div class="logo-box">
            <span class="logo-diamond"><span class="logo-diamond-inner" /></span>
          </div>
        </div>
        <h2 class="card-title">毕业设计管理系统</h2>
        <p class="card-sub">GRADUATION DESIGN MANAGEMENT SYSTEM</p>

        <!-- Tab: 左对齐 + 底部分隔线 -->
        <div class="tabs">
          <div class="tab" :class="{ active: activeTab === 'account' }" @click="switchTab('account')">账号登录</div>
          <div class="tab" :class="{ active: activeTab === 'captcha' }" @click="switchTab('captcha')">验证码登录</div>
        </div>

        <el-form @submit.prevent>
          <div class="field">
            <el-icon class="field-icon"><User /></el-icon>
            <el-input v-model="form.username" placeholder="请输入学号 / 工号 / 用户名" size="large"
                      @keyup.enter="handleLogin" />
          </div>
          <div class="field">
            <el-icon class="field-icon"><Lock /></el-icon>
            <el-input v-model="form.password" type="password" placeholder="请输入密码" size="large" show-password
                      @keyup.enter="handleLogin" />
          </div>

          <div class="field-row">
            <el-checkbox v-model="remember" label="记住密码" />
            <a class="link" @click.prevent="forgot">忘记密码？</a>
          </div>

          <!-- 滑块验证 -->
          <div ref="trackRef" class="slider" :class="{ verified: sliderVerified }">
            <div v-if="!sliderVerified" class="slider-progress" :style="{ width: sliderX + HANDLE + 'px' }" />
            <div class="slider-text">
              <template v-if="sliderVerified">✓ 验证通过</template>
              <template v-else>请按住滑块拖动到最右侧</template>
            </div>
            <div class="slider-handle" :style="{ left: sliderX + 'px' }" @pointerdown="onHandleDown">
              <el-icon v-if="!sliderVerified" color="#1a76fd"><DoubleRight /></el-icon>
              <el-icon v-else color="#4fc253"><Select /></el-icon>
            </div>
          </div>

          <el-button type="primary" size="large" class="login-btn" :loading="loading" @click="handleLogin">
            登 录
          </el-button>
        </el-form>

        <div class="divider"><span>其他登录方式</span></div>

        <div class="third-party">
          <div class="circle wechat" @click="thirdParty('微信')"><el-icon><ChatDotRound /></el-icon></div>
          <div class="circle qq" @click="thirdParty('QQ')"><el-icon><ChatLineRound /></el-icon></div>
          <div class="circle alipay" @click="thirdParty('支付宝')"><el-icon><Wallet /></el-icon></div>
          <div class="circle dingtalk" @click="thirdParty('钉钉')"><el-icon><ChatLineSquare /></el-icon></div>
        </div>

        <div class="demo-row">
          <span>演示账号:</span>
          <a v-for="d in demos" :key="d.label" @click.prevent="fillDemo(d)">{{ d.label }}</a>
        </div>

        <p class="footer-hint">还没有账号？<span class="link">联系管理员创建账号</span></p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  background: #e5f1fd; /* 设计图页面底色(浅蓝) */
}

/* ---------- 左侧插画 ---------- */
.showcase {
  flex: 1.5;
  position: relative;
  background-size: cover;
  background-position: top center; /* 竖构图设计图从顶部对齐, 保证标题完整 */
  overflow: hidden;
}

.brand {
  position: absolute;
  top: 36px;
  left: 52px;
  color: #1e2a4a;
}

.brand h1 {
  margin: 0;
  font-size: 34px;
  font-weight: 700;
  letter-spacing: 2px;
}

.brand p {
  margin: 10px 0 0;
  font-size: 15px;
  color: #6b7fa3;
  letter-spacing: 3px;
}

.feature {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(255, 255, 255, 0.96);
  border-radius: 12px;
  padding: 10px 14px;
  box-shadow: 0 8px 24px rgba(30, 42, 74, 0.1);
}

.feature-icon {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #f0f6ff;
  border: 1px solid #e1ecfb;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.feature-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #1a76fd;
  box-shadow: 0 0 0 4px rgba(26, 118, 253, 0.16);
}

.feature-title {
  font-size: 14px;
  font-weight: 700;
  color: #1e2a4a;
}

.feature-desc {
  font-size: 11px;
  color: #8a97ad;
  margin-top: 2px;
}

.showcase-footer {
  position: absolute;
  bottom: 16px;
  right: 26px;
  font-size: 12px;
  color: #93a5bd;
  letter-spacing: 1px;
  background: rgba(255, 255, 255, 0.55);
  padding: 2px 10px;
  border-radius: 10px;
}

/* ---------- 右侧登录卡 ---------- */
.panel {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 430px;
}

.card {
  width: 432px;
  background: #fff;
  border-radius: 16px;
  padding: 36px 44px 24px;
  box-shadow: 0 12px 40px rgba(30, 42, 74, 0.1);
  text-align: center;
}

/* 三层菱形 Logo */
.logo-box {
  width: 56px;
  height: 56px;
  margin: 0 auto;
  border-radius: 14px;
  background: #e8f1fd;
  border: 1px solid #dbe9fb;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-diamond {
  width: 26px;
  height: 26px;
  background: #1a76fd;
  border-radius: 5px;
  transform: rotate(45deg);
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-diamond-inner {
  width: 9px;
  height: 9px;
  background: #fff;
  border-radius: 2px;
  display: block;
}

.card-title {
  margin: 16px 0 0;
  font-size: 23px;
  font-weight: 700;
  color: #1e2a4a;
  letter-spacing: 1px;
}

.card-sub {
  margin: 9px 0 24px;
  font-size: 10px;
  letter-spacing: 2.2px;
  color: #a8b3c5;
}

/* Tab(左对齐) */
.tabs {
  display: flex;
  gap: 40px;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 24px;
}

.tab {
  position: relative;
  padding-bottom: 12px;
  font-size: 15px;
  color: #8a97ad;
  cursor: pointer;
  transition: color 0.2s;
}

.tab.active {
  color: #1a76fd;
  font-weight: 600;
}

.tab.active::after {
  content: '';
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: -1.5px;
  width: 36px;
  height: 3px;
  border-radius: 2px;
  background: #1a76fd;
}

/* 输入框 */
.field {
  position: relative;
  margin-bottom: 18px;
}

.field-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: #9dafc0;
  z-index: 2;
}

.field :deep(.el-input__wrapper) {
  border-radius: 8px;
  height: 48px;
  padding-left: 38px;
  box-shadow: 0 0 0 1px #dde3ec;
}

.field :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #1a76fd;
}

.field-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 2px 0 16px;
}

.link {
  font-size: 13px;
  color: #1a76fd;
  cursor: pointer;
}

/* 滑块验证 */
.slider {
  position: relative;
  height: 48px;
  border-radius: 8px;
  background: linear-gradient(90deg, #eef5fe, #dceafd);
  overflow: hidden;
  user-select: none;
  margin-bottom: 22px;
}

.slider.verified {
  background: linear-gradient(90deg, #e8f8e9, #d5f2d8);
  border: 1px solid #bfe8c4;
}

.slider-progress {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: linear-gradient(90deg, #cfe4fd, #a9ccfb);
  opacity: 0.65;
}

.slider-text {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: #8a97ad;
  letter-spacing: 1px;
}

.slider.verified .slider-text {
  color: #3e9e44;
  font-weight: 600;
}

.slider-handle {
  position: absolute;
  top: 2px;
  left: 0;
  width: 44px;
  height: 44px;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 2px 8px rgba(30, 42, 74, 0.18);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: grab;
  font-size: 18px;
  touch-action: none;
}

.slider-handle:active {
  cursor: grabbing;
}

/* 登录按钮 */
.login-btn {
  width: 100%;
  height: 50px;
  border-radius: 8px;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 8px;
  background: #1a76fd;
  border: none;
}

.login-btn:hover {
  background: #3b8bfc;
}

/* 其他登录方式 */
.divider {
  display: flex;
  align-items: center;
  margin: 26px 0 18px;
  color: #b6c0cf;
  font-size: 12px;
}

.divider::before,
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: #ebeef5;
}

.divider span {
  padding: 0 12px;
}

.third-party {
  display: flex;
  justify-content: center;
  gap: 26px;
}

.circle {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 18px;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.circle:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 14px rgba(30, 42, 74, 0.2);
}

.wechat { background: #34c759; }
.qq { background: #4a90e2; }
.alipay { background: #ff6a3d; }
.dingtalk { background: #8e7cf5; }

/* 演示账号与底部提示 */
.demo-row {
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px dashed #ebeef5;
  font-size: 12px;
  color: #8a97ad;
  display: flex;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}

.demo-row a {
  color: #1a76fd;
  cursor: pointer;
  text-decoration: none;
}

.demo-row a:hover {
  text-decoration: underline;
}

.footer-hint {
  margin: 14px 0 0;
  font-size: 12px;
  color: #a8b3c5;
}

/* ---------- 响应式 ---------- */
@media (max-width: 960px) {
  .showcase { display: none; }
  .panel { min-width: unset; }
}
</style>
