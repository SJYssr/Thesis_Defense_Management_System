// Run with Node >=22.18: node scripts/verify-practice-reminders.mjs
import assert from 'node:assert/strict'
import { FaceReminder } from '../src/utils/practiceFaceReminder.ts'

const tracker = new FaceReminder()
const events = []
let time = 0
function frames(faces, repeats) {
  for (let i = 0; i < repeats; ++i) {
    const event = tracker.sample(faces, time)
    if (event) events.push(event)
    time += 700
  }
}

frames(0, 8)
frames(1, 1)
frames(0, 8)
assert.deepEqual(events, [], 'brief absences separated by a face cannot accumulate')
tracker.pause()
frames(0, 10)
frames(0, 10)
assert.deepEqual(events, ['NO_FACE'])
tracker.pause()
frames(0, 10)
frames(1, 1)
frames(0, 10)
assert.deepEqual(events, ['NO_FACE'], 'invalid frames or a single normal frame cannot rearm an alarm')
frames(1, 2)
frames(0, 10)
assert.deepEqual(events, ['NO_FACE', 'NO_FACE'])
frames(2, 5)
time += 6000
frames(2, 3)
assert.equal(events.length, 2, 'a suspended timer cannot count as observed multi-face time')
frames(2, 5)
frames(3, 8)
assert.deepEqual(events, ['NO_FACE', 'NO_FACE', 'MULTIPLE_FACES'])
console.log('镜头提醒持续时长、暂停、去重与恢复断言通过')
