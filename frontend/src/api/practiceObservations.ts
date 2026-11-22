import type { AxiosRequestConfig } from 'axios'
import request from './request'

export type ObservationSignal = 'ENABLED' | 'NO_FACE' | 'MULTIPLE_FACES' | 'TAB_HIDDEN'
export interface ObservationCounts { no_face: number; multiple_faces: number; tab_hidden: number }
interface ObservationSnapshot extends ObservationCounts { run_id: string }

const counterKey = { NO_FACE: 'no_face', MULTIPLE_FACES: 'multiple_faces', TAB_HIDDEN: 'tab_hidden' } as const
// Keep at most the latest snapshot of each observation run. Reloaded tabs can retry unacknowledged counts.
const pending = new Map<string, string>()
const silentRequest: AxiosRequestConfig & { _silent: boolean } = { _silent: true, timeout: 8000 }

function prefix(userId: number, sessionId: number) { return `practice-observations:${userId}:${sessionId}:` }

function remember(key: string, value: string) {
  pending.set(key, value)
  try { sessionStorage.setItem(key, value) } catch { /* Memory remains available in restricted browsers. */ }
}

export async function syncObservations(userId: number, sessionId: number): Promise<boolean> {
  const scope = prefix(userId, sessionId)
  try {
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i)
      if (key?.startsWith(scope) && !pending.has(key)) {
        const value = sessionStorage.getItem(key)
        if (value) pending.set(key, value)
      }
    }
  } catch { /* Browsers can restrict storage independently of device permissions. */ }
  const snapshots = [...pending].filter(([key]) => key.startsWith(scope))
  const results = await Promise.all(snapshots.map(async ([key, value]) => {
    try {
      await request.put(`/ai/defense/sessions/${sessionId}/observations`, JSON.parse(value), silentRequest)
    } catch { return false }
    if (pending.get(key) === value) {
      pending.delete(key)
      try { sessionStorage.removeItem(key) } catch { /* Memory is already in sync. */ }
    }
    return true
  }))
  return results.every(Boolean)
}

export function createObservationRecorder(userId: number, sessionId: number) {
  // One stream per mounted practice, including tabs opened by duplicating an existing tab.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  const runId = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('')
  const counts: ObservationSnapshot = { run_id: runId, no_face: 0, multiple_faces: 0, tab_hidden: 0 }
  return (signal: ObservationSignal) => {
    if (signal !== 'ENABLED') counts[counterKey[signal]]++
    remember(prefix(userId, sessionId) + runId, JSON.stringify(counts))
    return syncObservations(userId, sessionId)
  }
}
