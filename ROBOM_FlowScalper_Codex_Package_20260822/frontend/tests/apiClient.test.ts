// HTTP 오류 계약과 timeout이 사용자용 한국어 상태로 변환되는지 검증한다.
import { afterEach, expect, test, vi } from 'vitest'
import { fetchJson } from '../src/api/client'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

test('preserves typed backend error details', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    detail: { error_code: 'CONTROL_CONFLICT', error_message_ko: '다른 작업이 진행 중입니다.', retryable: true },
  }), { status: 409, headers: { 'Content-Type': 'application/json' } })))
  await expect(fetchJson('/api/control/start-live')).rejects.toMatchObject({
    status: 409,
    code: 'CONTROL_CONFLICT',
    messageKo: '다른 작업이 진행 중입니다.',
    retryable: true,
  })
})

test('aborts a stalled request at the explicit timeout', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })))
  const pending = fetchJson('/api/dashboard', {}, 100)
  const expectation = expect(pending).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', retryable: true })
  await vi.advanceTimersByTimeAsync(101)
  await expectation
})
