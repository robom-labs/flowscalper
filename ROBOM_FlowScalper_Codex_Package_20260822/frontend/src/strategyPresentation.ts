// Backend Strategy Registry의 family·variant 표시 계약을 화면 전체에서 공유한다.
import type { StrategySummaryRow } from './types'

export const modeLabels: Record<StrategySummaryRow['mode'], string> = {
  ACTIVE: '공동·독립 모의 중',
  SHADOW: '독립 모의 중',
  OFF: '검증 중지',
}

export function strategyLabel(strategy: StrategySummaryRow | undefined, strategyId: string) {
  if (!strategy || strategy.strategy_id !== strategyId) return '알 수 없는 이전 전략'
  const family = strategy.family_label_ko?.trim() || strategy.short_name
  const variant = strategy.variant_label_ko?.trim() || strategy.display_name_ko
  return family === variant ? family : `${family} · ${variant}`
}

export function orderedStrategies(strategies: StrategySummaryRow[]) {
  return [...strategies]
}

export function strategyWaitReasonLabel(_code: string, reasonKo?: string | null) {
  return reasonKo?.trim() || '세부 조건 대기'
}
