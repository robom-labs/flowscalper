// 서버·로그·차트 시간을 한국 표준시로 일관되게 표시한다.
import type { Time } from 'lightweight-charts'

const TIME_ZONE = 'Asia/Seoul'

const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const dateTimeFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

export function formatKstTime(tsMs: number) {
  return timeFormatter.format(new Date(tsMs))
}

export function formatKstDateTime(tsMs: number) {
  return `${dateTimeFormatter.format(new Date(tsMs))} KST`
}

export function formatChartKstTime(time: Time) {
  if (typeof time !== 'number') return String(time)
  return formatKstTime(time * 1_000)
}
