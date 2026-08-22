// 브라우저 시간대와 무관하게 한국 표준시를 표시하는지 검증한다.
import { expect, test } from 'vitest'
import type { UTCTimestamp } from 'lightweight-charts'
import { formatChartKstTime, formatKstDateTime, formatKstTime } from '../src/time'

test('formats event and chart timestamps in Asia/Seoul', () => {
  expect(formatKstTime(0)).toBe('09:00:00')
  expect(formatChartKstTime(0 as UTCTimestamp)).toBe('09:00:00')
  expect(formatKstDateTime(0)).toContain('1970. 01. 01.')
  expect(formatKstDateTime(0).endsWith('KST')).toBe(true)
})
