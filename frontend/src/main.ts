import { createApp } from 'vue'
import { createPinia } from 'pinia'
// ElMessage / ElMessageBox 是函数式 API, 不经过模板解析, 样式需显式引入(各自已带上依赖的基础样式)
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
// 图标按需注册(D76): 原实现 Object.entries(ElementPlusIconsVue) 全量注册 1000+ 个图标,
// 实际模板只用到 ICONS 里的这些; 新增图标时在这里补一行即可。
import {
  ArrowDown,
  ArrowRight,
  Bell,
  Check,
  Clock,
  CloseBold,
  Delete,
  Document,
  Download,
  Expand,
  Fold,
  FullScreen,
  HomeFilled,
  Loading,
  Location,
  MagicStick,
  Plus,
  Promotion,
  Refresh,
  Search,
  Select,
  Setting,
  Upload,
  VideoPlay,
} from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { useUserStore } from './stores/user'
import './styles/index.css'

const ICONS = {
  ArrowDown, ArrowRight, Bell, Check, Clock, CloseBold, Delete, Document,
  Download, Expand, Fold, FullScreen, HomeFilled, Loading, Location, MagicStick,
  Plus, Promotion, Refresh, Search, Select, Setting, Upload, VideoPlay,
}

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
// 先注册 profile 同步回调，再挂 router：路由守卫里就可能触发 refresh
useUserStore(pinia).bindProfileSync()
app.use(router)
// Element Plus 组件与指令由 unplugin-vue-components 按需引入(见 vite.config.ts);
// 中文 locale 不再走 app.use(ElementPlus, { locale }), 改由 App.vue 的 <el-config-provider> 提供。
for (const [name, icon] of Object.entries(ICONS)) {
  app.component(name, icon)
}
app.mount('#app')
