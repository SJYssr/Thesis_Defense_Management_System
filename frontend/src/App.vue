<template>
  <!-- Element Plus 改为按需引入后, 中文 locale 由 ConfigProvider 提供(D76) -->
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>
