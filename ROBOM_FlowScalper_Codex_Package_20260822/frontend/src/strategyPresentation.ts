// 전략 Registry의 고정 순서와 비전문가용 표시 문구를 공유한다.
import type { StrategyRow } from './types'

export const strategyOrder = [
  'LSA_REVERSAL_V1',
  'CBR_CONTINUATION_V1',
  'VWAP_EXHAUSTION_REVERSION_V1',
  'OFI_CONTINUATION_PULLBACK_V1',
  'QUEUE_MICROPRICE_MOMENTUM_V1',
  'AGGRESSOR_FLOW_CONTINUATION_V1',
] as const

export const modeLabels: Record<StrategyRow['mode'], string> = {
  ACTIVE: '리그 + 공동계좌',
  SHADOW: '리그에서만 테스트',
  OFF: '사용 안 함',
}

export function strategyLabel(strategy: StrategyRow | undefined, strategyId: string) {
  return strategy ? `${strategy.short_name} · ${strategy.display_name_ko}` : strategyId
}

export function orderedStrategies(strategies: StrategyRow[]) {
  const order = new Map<string, number>(strategyOrder.map((strategyId, index) => [strategyId, index]))
  return [...strategies].sort((left, right) => (
    (order.get(left.strategy_id) ?? Number.MAX_SAFE_INTEGER)
    - (order.get(right.strategy_id) ?? Number.MAX_SAFE_INTEGER)
  ))
}
