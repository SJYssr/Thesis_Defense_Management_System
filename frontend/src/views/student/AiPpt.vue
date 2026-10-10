<script setup lang="ts">
import { computed, ref } from 'vue'
import { aiApi } from '@/api/common'
import { useUserStore } from '@/stores/user'
const user = useUserStore()
const key = `defense-ppt-${user.user?.id}`
let saved: any = null
try { saved = JSON.parse(sessionStorage.getItem(key) || 'null') } catch { /* Ignore stale local drafts. */ }
const outline = ref<any>(saved)
const selected = ref(0)
const busy = ref(false)
const page = computed(() => outline.value?.pages?.[selected.value])
const isCore = computed(() => /架构|RAG|AI|核心|实现/.test(page.value?.title || ''))
async function generate() { busy.value = true; try { outline.value = (await aiApi.pptOutline()).data; selected.value = 0; sessionStorage.setItem(key, JSON.stringify(outline.value)) } finally { busy.value = false } }
function download() {
  const text = `# ${outline.value.title}\n\n` + outline.value.pages.map((p: any) => `## ${p.page}. ${p.title}\n\n页面目标：${p.goal || p.title}\n\n${p.key_points.map((k: string) => '- ' + k).join('\n')}\n\n讲解提示：${p.speaker_notes}\n\n建议图表：${p.recommended_visual}`).join('\n\n')
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = '答辩PPT大纲.md'; a.click(); URL.revokeObjectURL(url)
}
</script>
<template>
  <header class="student-heading"><h1>AI 答辩 PPT 大纲</h1><p>根据论文文档、阶段材料和模拟答辩结果自动生成演示结构</p></header>
  <div class="ppt-layout" v-loading="busy">
    <aside class="page-card"><h3 class="block-title">页面结构</h3><button v-for="(p, i) in outline?.pages || []" :key="p.page" class="ppt-nav" :class="{ active: selected === i }" @click="selected = Number(i)">{{ String(p.page).padStart(2, '0') }} {{ p.title }}</button><el-button :loading="busy" @click="generate">{{ outline ? '重新生成' : '生成大纲' }}</el-button></aside>
    <section class="page-card ppt-content" v-if="page">
      <div class="ppt-title-row">
        <h2>第 {{ String(page.page).padStart(2, '0') }} 页 · {{ page.title }}</h2>
        <el-tag v-if="isCore" size="small" type="warning">核心页</el-tag>
      </div>
      <h4>页面目标</h4>
      <p>{{ page.goal || page.title }}</p>
      <h4>核心要点</h4>
      <div v-for="(point, i) in page.key_points" :key="i" class="muted-row">· {{ point }}</div>
      <h4>推荐视觉</h4>
      <div class="ppt-hints">
        <div><strong>建议图表</strong><p>{{ page.recommended_visual }}</p></div>
        <div><strong>讲解提示</strong><p>{{ page.speaker_notes }}</p></div>
      </div>
      <div class="ppt-footer"><el-button type="primary" @click="download">导出大纲</el-button></div>
    </section>
    <section class="page-card" v-else><el-empty description="上传论文并完成索引后，可生成答辩 PPT 大纲"><el-button type="primary" :loading="busy" @click="generate">生成大纲</el-button></el-empty></section>
  </div>
</template>
<style scoped>
.ppt-layout { display: grid; grid-template-columns: 270px minmax(0, 1fr); gap: 22px; }
.ppt-layout > .page-card { min-height: 650px; }
.ppt-nav { display: block; width: 100%; padding: 13px; text-align: left; border: 1px solid #eef0f5; background: #f8f9fc; border-radius: 5px; margin-bottom: 10px; color: #586478; font-size: 12px; cursor: pointer; }
.ppt-nav.active { color: #1878fd; background: #edf4ff; border-color: #81b8ff; }
.ppt-title-row { display: flex; align-items: center; gap: 12px; }
.ppt-content h2 { font-size: 21px; margin: 0 0 18px; }
.ppt-content h4 { margin: 26px 0 14px; font-size: 14px; }
.ppt-content > p { font-size: 13px; color: #9aa2af; }
.ppt-hints { display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; }
.ppt-hints > div { background: #edf4ff; padding: 18px; border-radius: 6px; }
.ppt-hints > div + div { background: #f1edff; }
.ppt-hints strong { color: #488cef; font-size: 13px; }
.ppt-hints p { color: #8a96a8; font-size: 12px; line-height: 1.8; }
.ppt-footer { display: flex; justify-content: flex-end; margin-top: 24px; }
@media (max-width: 800px) { .ppt-layout { grid-template-columns: 1fr; } .ppt-layout > .page-card { min-height: 0; } .ppt-hints { grid-template-columns: 1fr; } }
</style>
