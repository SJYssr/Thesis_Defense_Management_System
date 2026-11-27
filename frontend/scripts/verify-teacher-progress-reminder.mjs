// Run with Node >=22.18. Exercise the actual teacher view handlers with local API stubs.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { runInNewContext } from 'node:vm'
import { computed, nextTick, reactive, ref, watch } from 'vue'

const source = readFileSync(new URL('../src/views/teacher/AiProgress.vue', import.meta.url), 'utf8')
const setup = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
const requests = []
const sends = []
const lifecycle = {}
const studentA = { student_id: 101, student_name: '学生甲' }
const studentB = { student_id: 102, student_name: '学生乙' }
const view = runInNewContext(stripTypeScriptTypes(setup) + `
  ;({ students, selectedId, analyse, analysis, analysisTime, busy, suggestedReminder,
     useSuggestion, remind, reminderVisible, remindTarget, reminderForm, sendReminder })
`, {
  ref, computed, watch, reactive, nextTick,
  onMounted: () => {},
  onBeforeUnmount: handler => { lifecycle.unmount = handler },
  aiApi: {
    analyseProgress: studentId => new Promise((resolve, reject) => { requests.push({ studentId, resolve, reject }) }),
  },
  progressApi: {
    sendReminder: (studentId, payload) => new Promise(resolve => { sends.push({ studentId, payload, resolve }) }),
  },
  ElMessage: { success: () => {} },
})
view.students.value = [studentA, studentB]

function resolve(index, reminder) {
  requests[index].resolve({ data: { summary: reminder, suggestions: ['模拟建议'], recommended_reminder: reminder } })
}

const first = view.analyse(101)
resolve(0, '仅发给学生甲')
await first
assert.ok(view.analysisTime.value)
assert.equal(view.suggestedReminder.value, '仅发给学生甲')
view.selectedId.value = 102
assert.equal(view.analysis.value, undefined)
assert.equal(view.analysisTime.value, undefined)
assert.equal(view.suggestedReminder.value, '')
view.useSuggestion()
assert.equal(view.reminderVisible.value, false, '切换学生后，即使旧按钮尚未重渲染也不能打开串人弹窗')

const failedB = view.analyse()
requests[1].reject(new Error('模拟 B 分析失败'))
await failedB
view.useSuggestion()
assert.equal(view.reminderVisible.value, false)

const lateA = view.analyse(101)
const freshB = view.analyse(102)
resolve(2, '晚回来的甲文案')
await lateA
assert.equal(view.analysis.value, undefined)
assert.equal(view.busy.value, true, '甲旧请求不能清空乙新请求的加载状态')
resolve(3, '仅发给学生乙')
await freshB
assert.equal(view.busy.value, false)
view.useSuggestion()
assert.equal(view.remindTarget.value.student_id, 102)
assert.equal(view.reminderForm.content, '仅发给学生乙')

view.selectedId.value = 101
const sending = view.sendReminder()
await view.sendReminder()
view.remind(studentA)
assert.equal(sends.length, 1)
assert.equal(sends[0].studentId, 102, '弹窗打开后，已确认的草稿与收件人保持绑定')
assert.equal(sends[0].payload.content, '仅发给学生乙')
assert.equal(view.remindTarget.value.student_id, 102, '发送中不能切换同一弹窗的收件人')
sends[0].resolve()
await sending
assert.equal(view.reminderVisible.value, false)

const obsoleteA = view.analyse(101)
view.selectedId.value = 102
view.selectedId.value = 101
const freshA = view.analyse()
resolve(5, '甲的新文案')
await freshA
resolve(4, '甲的旧文案')
await obsoleteA
assert.equal(view.suggestedReminder.value, '甲的新文案', 'A → B → A 的旧请求也不能覆盖新分析')

const unmounted = view.analyse(102)
lifecycle.unmount()
resolve(6, '页面已关闭')
await unmounted
assert.equal(view.suggestedReminder.value, '')
assert.equal(view.busy.value, false)
console.log('教师 AI 进度分析：切换、失败、乱序、回切、卸载和收件人绑定回归通过')

const detailSource = readFileSync(new URL('../src/views/teacher/StudentProgress.vue', import.meta.url), 'utf8')
const detailSetup = detailSource.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
const details = []
const analyses = []
const detailPage = runInNewContext(stripTypeScriptTypes(detailSetup) +
  ';({ openDetail, analyse, detail, detailVisible, analysis, analysing })', {
  ref, computed, watch, reactive, onMounted() {}, onBeforeUnmount() {},
  progressApi: { teacherStudentDetail: () => new Promise(resolve => details.push(resolve)) },
  aiApi: { analyseProgress: () => new Promise(resolve => analyses.push(resolve)) },
})
const detailA = detailPage.openDetail(studentA)
const detailB = detailPage.openDetail(studentB)
details[1]({ data: studentB })
await detailB
details[0]({ data: studentA })
await detailA
assert.equal(detailPage.detail.value.student_id, 102)
const analysingB = detailPage.analyse()
detailPage.detailVisible.value = false
const reopenA = detailPage.openDetail(studentA)
details[2]({ data: studentA })
await reopenA
const analysingA = detailPage.analyse()
analyses[0]({ data: { summary: '乙的概览' } })
await analysingB
assert.equal(detailPage.analysing.value, true)
assert.equal(detailPage.analysis.value, null)
analyses[1]({ data: { summary: '甲的概览' } })
await analysingA
assert.equal(detailPage.analysis.value.summary, '甲的概览')
console.log('教师学生详情：连续选人、关闭重开和迟到分析回包回归通过')
