import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 按需引入(D76): 只打包模板里真正用到的组件与指令样式。
    // 全量 `app.use(ElementPlus)` + 全量 icons 曾让主 chunk 达到 882 kB, 其中业务代码仅约 21 kB。
    Components({
      // 不生成 components.d.ts: 全局组件声明会让 vue-tsc 把 el-table 的 slot row 推成
      // DefaultRow, 从而炸出既有代码里一批原本被 any 掩盖的 row 类型不匹配(MyTopics/TaskManage 等)。
      // 这里只取"按需打包"本身, 类型收紧另开一批单独做。
      dts: false,
      // 只解析 el-* (以及 v-loading 等指令), 不接管本地 src/components 的显式 import
      dirs: [],
      resolvers: [ElementPlusResolver({ importStyle: 'css', directives: true })],
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
