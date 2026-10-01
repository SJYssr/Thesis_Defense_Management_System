<template>
  <div class="my-selection">
    <header class="page-heading"><h1>我的选题</h1><p>查看当前选题申请及审核状态</p></header>

    <!-- 当前选题 -->
    <section class="current-card">
      <div class="cc-head">
        <h3>当前选题</h3>
        <span v-if="current" class="status-tag" :class="statusClass(current.status)">{{ statusCn(current.status) }}</span>
      </div>
      <template v-if="current">
        <h2 class="cc-title">{{ current.topic_title || '题目已删除' }}</h2>
        <div class="cc-grid">
          <div class="cc-field"><span class="k">指导教师</span><b>{{ current.teacher_name || '待确认' }}</b></div>
          <div class="cc-field"><span class="k">申请时间</span><b>{{ fmt(current.created_at) }}</b></div>
          <div class="cc-field"><span class="k">审核时间</span><b>{{ fmt(current.review_time) }}</b></div>
          <div class="cc-field"><span class="k">审核人</span><b>{{ current.reviewer_name || '—' }}</b></div>
        </div>
        <div class="cc-comment">
          <span class="k">审核意见</span>
          <p>{{ current.review_comment || '暂无审核意见' }}</p>
        </div>
      </template>
      <el-empty v-else :description="items.length ? '当前没有进行中的选题申请' : '尚未申请题目'" :image-size="80">
        <el-button type="primary" @click="$router.push('/student/topics')">前往题目中心</el-button>
      </el-empty>
    </section>

    <!-- 选题记录 -->
    <section class="record-card">
      <h3 class="rc-title">选题记录</h3>
      <div class="record-table-wrap">
        <table class="record-table">
          <thead>
            <tr><th>时间</th><th>题目</th><th>操作</th><th>操作人</th><th>状态</th><th class="op-col">操作</th></tr>
          </thead>
          <tbody>
            <template v-for="rec in pagedRecords" :key="rec.key">
              <tr>
                <td>{{ rec.time }}</td>
                <td class="topic-cell" :title="rec.topic">{{ rec.topic }}</td>
                <td>{{ rec.action }}</td>
                <td>{{ rec.operator }}</td>
                <td><span class="status-tag" :class="rec.cls">{{ rec.status }}</span></td>
                <td class="op-col">
                  <el-button v-if="rec.cancelable" link type="danger" size="small" @click="cancel(rec.raw)">撤销申请</el-button>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        <el-empty v-if="!records.length" description="暂无选题记录" :image-size="70" />
      </div>
      <div v-if="records.length > pageSize" class="record-pager">
        <span class="page-total">共 {{ records.length }} 条</span>
        <el-pagination
          v-model:current-page="page" :page-size="pageSize" :total="records.length"
          background layout="prev, pager, next" small
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { selectionApi } from '@/api/business'

const items = ref<any[]>([])
const page = ref(1)
const pageSize = 10

const current = computed(() =>
  items.value.find(i => i.status === 'APPROVED' || i.status === 'PENDING'))

interface RecordRow {
  key: string; time: string; topic: string; action: string
  operator: string; status: string; cls: string; cancelable: boolean; raw: any
}

const records = computed<RecordRow[]>(() => {
  const rows: RecordRow[] = []
  for (const s of items.value) {
    const time = fmt(s.created_at)
    rows.push({
      key: `${s.id}-submit`, time, topic: s.topic_title || '题目已删除',
      action: '提交选题申请', operator: '学生',
      status: s.status === 'PENDING' ? '待审核' : '已提交',
      cls: s.status === 'PENDING' ? 's-pending' : 's-info',
      cancelable: s.status === 'PENDING', raw: s,
    })
    if (s.review_time) {
      const approved = s.status === 'APPROVED'
      rows.push({
        key: `${s.id}-review`, time: fmt(s.review_time), topic: s.topic_title || '题目已删除',
        action: approved ? '教师审核通过' : '教师审核驳回',
        operator: s.reviewer_name || '教师',
        status: approved ? '已通过' : s.status === 'REJECTED' ? '未通过' : '已撤销',
        cls: approved ? 's-passed' : s.status === 'REJECTED' ? 's-failed' : 's-withdraw',
        cancelable: false, raw: s,
      })
    }
  }
  return rows
})

const pagedRecords = computed(() =>
  records.value.slice((page.value - 1) * pageSize, page.value * pageSize))

async function load() {
  const res = await selectionApi.mine()
  items.value = res.data
}

async function cancel(row: any) {
  await ElMessageBox.confirm('确定撤销该选题申请吗？撤销后可重新申请其他题目。', '撤销申请', { type: 'warning' })
  await selectionApi.cancel(row.id)
  ElMessage.success('已撤销')
  await load()
}

function statusCn(status: string) {
  return ({ PENDING: '待审核', APPROVED: '已通过', REJECTED: '未通过', CANCELLED: '已撤销' } as Record<string, string>)[status] || status
}
function statusClass(status: string) {
  return ({ PENDING: 's-pending', APPROVED: 's-passed', REJECTED: 's-failed', CANCELLED: 's-withdraw' } as Record<string, string>)[status] || 's-withdraw'
}
function fmt(value?: string | null) { return (value || '—').replace('T', ' ').slice(0, 16) }

onMounted(load)
</script>

<style scoped>
.page-heading { margin-bottom: 18px; }
.page-heading h1 { font-size: 26px; color: #1f2d3d; margin: 0 0 6px; }
.page-heading p { color: #93a0b4; font-size: 13px; margin: 0; }

/* 当前选题卡 */
.current-card {
  background: #fff;
  border-radius: 10px;
  padding: 24px 28px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 16px;
}
.cc-head { display: flex; align-items: center; justify-content: space-between; }
.cc-head h3 { font-size: 16px; color: #1f2d3d; margin: 0; }
.cc-title { font-size: 24px; color: #1f2d3d; margin: 22px 0 24px; line-height: 1.4; }
.cc-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px 40px;
  margin-bottom: 20px;
}
.cc-field { display: flex; align-items: baseline; gap: 16px; font-size: 13.5px; }
.cc-field .k { width: 64px; flex-shrink: 0; color: #93a0b4; font-size: 12.5px; }
.cc-field b { color: #1f2d3d; font-weight: 600; }
.cc-comment { display: flex; gap: 16px; font-size: 13.5px; }
.cc-comment .k { width: 64px; flex-shrink: 0; color: #93a0b4; font-size: 12.5px; padding-top: 2px; }
.cc-comment p { flex: 1; margin: 0; color: #3b4a5f; line-height: 1.7; background: #f8fafc; border-radius: 8px; padding: 10px 14px; }

/* 选题记录 */
.record-card {
  background: #fff;
  border-radius: 10px;
  padding: 24px 28px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.rc-title { font-size: 16px; color: #1f2d3d; margin: 0 0 18px; }
.record-table-wrap { border: 1px solid #eef2f8; border-radius: 10px; overflow: hidden; }
.record-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.record-table th {
  background: #f5f8fd;
  color: #5a6b80;
  font-weight: 600;
  font-size: 12.5px;
  text-align: left;
  padding: 11px 16px;
}
.record-table td { padding: 12px 16px; border-top: 1px solid #f0f3f8; color: #3b4a5f; }
.topic-cell { max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.op-col { width: 90px; text-align: center; }
.muted { color: #c2cbd8; }
.record-pager { display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding-top: 14px; }
.page-total { font-size: 12.5px; color: #8a97a8; }

/* 状态标签 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.s-pending { color: #e67e22; background: #fdf1e3; }
.s-passed { color: #27ae60; background: #e4f7ec; }
.s-failed { color: #e74c3c; background: #fdeaea; }
.s-info { color: #1878fd; background: #e3effe; }
.s-withdraw { color: #8a97ad; background: #eef1f5; }

@media (max-width: 700px) {
  .cc-grid { grid-template-columns: 1fr; gap: 12px; }
  .record-table th:nth-child(2), .record-table td:nth-child(2) { display: none; }
  .record-table-wrap { overflow-x: auto; }
  .record-table { min-width: 560px; }
}
</style>
