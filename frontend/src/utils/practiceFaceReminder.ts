/** 仅持续出现在有效视频帧中的现象触发提醒，连续正常画面才结束该次提醒。 */
export class FaceReminder {
  private candidate: number | null = null
  private since = 0
  private frames = 0
  private lastAt = 0
  private reported: number | null = null

  pause() {
    this.candidate = null
    this.frames = 0
    this.lastAt = 0
  }

  reset() {
    this.pause()
    this.reported = null
  }

  sample(count: number, now: number): 'NO_FACE' | 'MULTIPLE_FACES' | undefined {
    const category = Math.min(count, 2)
    if (now - this.lastAt > 2100) this.pause()
    this.lastAt = now
    if (this.candidate !== category) {
      this.candidate = category
      this.since = now
      this.frames = 0
    }
    this.frames++
    if (category === 1) {
      if (this.frames >= 2) this.reported = null
      return
    }
    const thresholdMs = category === 0 ? 6000 : 4000
    if (category !== this.reported && this.frames >= 4 && now - this.since >= thresholdMs) {
      this.reported = category
      return category === 0 ? 'NO_FACE' : 'MULTIPLE_FACES'
    }
  }
}
