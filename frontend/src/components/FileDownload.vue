<script setup lang="ts">
import { ref } from 'vue'
import request from '@/api/request'

const props = defineProps<{ fileId: number; filename: string; archive?: boolean }>()
const loading = ref(false)

async function download() {
  if (loading.value) return
  loading.value = true
  try {
    const path = props.archive ? '/archive-files/' : '/files/'
    const blob = await request.get<unknown, Blob>(path + props.fileId + '/download', { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = props.filename
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    // 请求拦截器统一提示下载失败。
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-button type="primary" link :loading="loading" @click="download"><slot>下载</slot></el-button>
</template>
