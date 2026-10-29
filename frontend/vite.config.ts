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
      // 不生成 components.d.ts。开启后 vue-tsc 会把 el-table 的 slot row 推成 DefaultRow,
      // 类型收紧随即暴露一批真实的不匹配: 实测 12 个文件 43 处(AdminTopics/AdminProgress/
      // AdminMaterials/AnnouncementAdmin/TeacherInfoPanel/StudentInfoPanel/AdminInfoPanel/
      // PromptAdmin/AcademicSchedule/MyTopics/TaskManage/MyArchive), 全是 `#default="{ row }"`
      // 里的 row 传给强类型函数。
      //
      // 试过的修法都不划算: 给 slot 加类型标注(`#default="{ row }: { row: XxxRow }"`)会被
      // el-table 固定的 slot 签名拒绝(Argument of type '(props: { row: XxxRow }) => any'
      // is not assignable to '(props: { row: DefaultRow; ... }) => any'), 只能逐处 `as` 断言
      // 43 次 —— 那是拿断言换检查, 收益不抵 12 个页面模板的改动风险。
      // 真要收紧, 正确做法是先给 el-table 包一层带泛型的表格组件, 再逐步迁移。
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
