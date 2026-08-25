// 원장의 정밀도는 유지하면서 화면에는 사람이 판단할 만큼만 숫자를 표시한다.
type Numeric = string | number | null | undefined

function parsed(value: Numeric) {
  const result = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(result) ? result : null
}

function localized(value: number, maximumFractionDigits: number, minimumFractionDigits = 0) {
  return value.toLocaleString('ko-KR', {
    maximumFractionDigits,
    minimumFractionDigits,
    useGrouping: true,
  })
}

function moneyDigits(value: number) {
  const magnitude = Math.abs(value)
  if (magnitude >= 100) return 2
  if (magnitude >= 1) return 3
  if (magnitude >= 0.01) return 4
  if (magnitude >= 0.0001) return 6
  return 8
}

export function formatUsdt(
  value: Numeric,
  options: { signed?: boolean; equity?: boolean } = {},
) {
  const number = parsed(value)
  if (number === null) return '—'
  const digits = options.equity ? 2 : moneyDigits(number)
  const minimum = options.equity || number === 0 ? 2 : 0
  const sign = options.signed && number > 0 ? '+' : ''
  return `${sign}${localized(number, digits, minimum)} USDT`
}

export function formatPrice(value: Numeric) {
  const number = parsed(value)
  if (number === null) return '—'
  const magnitude = Math.abs(number)
  const digits = magnitude >= 1_000 ? 2 : magnitude >= 100 ? 3 : magnitude >= 1 ? 4 : magnitude >= 0.01 ? 6 : 8
  return localized(number, digits)
}

export function formatQuantity(value: Numeric) {
  const number = parsed(value)
  if (number === null) return '—'
  const magnitude = Math.abs(number)
  const digits = magnitude >= 1_000 ? 0 : magnitude >= 100 ? 2 : magnitude >= 1 ? 3 : 6
  return localized(number, digits)
}

export function formatRatio(value: Numeric, suffix = '') {
  const number = parsed(value)
  return number === null ? '—' : `${localized(number, 2)}${suffix}`
}

export function formatPercentFraction(value: Numeric) {
  const number = parsed(value)
  return number === null ? '—' : `${localized(number * 100, 1)}%`
}

export function formatPercentValue(value: Numeric) {
  const number = parsed(value)
  return number === null ? '—' : `${localized(number, 2)}%`
}

export function formatDurationMs(value: Numeric) {
  const milliseconds = parsed(value)
  if (milliseconds === null) return '—'
  const seconds = Math.max(0, milliseconds) / 1_000
  if (seconds < 10) return `${localized(seconds, 1)}초`
  if (seconds < 60) return `${localized(seconds, 0)}초`
  const minutes = Math.floor(seconds / 60)
  const remaining = Math.round(seconds % 60)
  return remaining ? `${minutes}분 ${remaining}초` : `${minutes}분`
}

export function paperAccountLabel(accountId: string) {
  if (accountId === 'SHARED_PAPER') return '공동계좌'
  if (accountId === 'REPLAY') return '저장 재생'
  return '전략 독립계좌'
}

export function formatCompactNumber(value: Numeric) {
  const number = parsed(value)
  if (number === null) return '—'
  const magnitude = Math.abs(number)
  if (magnitude >= 1_000_000_000_000) return `${localized(number / 1_000_000_000_000, 1)}조`
  if (magnitude >= 100_000_000) return `${localized(number / 100_000_000, 1)}억`
  if (magnitude >= 10_000) return `${localized(number / 10_000, 1)}만`
  return localized(number, 1)
}

const exitReasonLabels: Record<string, string> = {
  EDGE_DECAY: '진입 근거 약화',
  EXIT_EDGE_DECAY: '진입 근거 약화',
  EXIT_PROFIT_PROTECTION: '이익 보호 종료',
  TAKE_PROFIT: '익절',
  TAKE_PROFIT_1: '1차 익절',
  TP1: '1차 익절',
  TP2: '2차 익절',
  STOP: '손절',
  STOP_LOSS: '손절',
  EXIT_EMERGENCY_STALE: '데이터 안전 종료',
  EMERGENCY_STALE: '데이터 안전 종료',
}

export function exitReasonLabel(value: string) {
  return exitReasonLabels[value] ?? value.replaceAll('_', ' ')
}
