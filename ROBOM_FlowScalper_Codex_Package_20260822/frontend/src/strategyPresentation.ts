// 전략 Registry의 고정 순서와 비전문가용 표시 문구를 공유한다.
import type { StrategyRow } from './types'

export const strategyOrder = [
  'LSA_REVERSAL_V1',
  'CBR_CONTINUATION_V1',
  'VWAP_EXHAUSTION_REVERSION_V1',
  'OFI_CONTINUATION_PULLBACK_V1',
  'QUEUE_MICROPRICE_MOMENTUM_V1',
  'AGGRESSOR_FLOW_CONTINUATION_V1',
  'MULTILEVEL_MICROPRICE_MOMENTUM_V1',
  'DEPTH_ADJUSTED_OFI_IMPULSE_V1',
] as const

export const modeLabels: Record<StrategyRow['mode'], string> = {
  ACTIVE: '공동·독립 모의 중',
  SHADOW: '독립 모의 중',
  OFF: '꺼짐',
}

const fallbackLabels: Record<string, string> = {
  LSA_REVERSAL_V1: 'LSA 반전 · 급락·급등 쓸기 반전',
  CBR_CONTINUATION_V1: 'CBR 돌파 · 압축 돌파 재가속',
  VWAP_EXHAUSTION_REVERSION_V1: 'VWAP 소진 · 과도이탈 평균복귀',
  OFI_CONTINUATION_PULLBACK_V1: 'OFI 눌림 · 추세 눌림 지속',
  QUEUE_MICROPRICE_MOMENTUM_V1: '호가 쏠림 · 순간추세',
  AGGRESSOR_FLOW_CONTINUATION_V1: '체결흐름 · 강한 체결 지속',
  MULTILEVEL_MICROPRICE_MOMENTUM_V1: '다중호가 · 10단계 공정가 추세',
  DEPTH_ADJUSTED_OFI_IMPULSE_V1: '깊이 OFI · 깊이보정 OFI 충격',
}

export function strategyLabel(strategy: StrategyRow | undefined, strategyId: string) {
  return strategy ? `${strategy.short_name} · ${strategy.display_name_ko}` : fallbackLabels[strategyId] ?? strategyId
}

export function orderedStrategies(strategies: StrategyRow[]) {
  const order = new Map<string, number>(strategyOrder.map((strategyId, index) => [strategyId, index]))
  return [...strategies].sort((left, right) => (
    (order.get(left.strategy_id) ?? Number.MAX_SAFE_INTEGER)
    - (order.get(right.strategy_id) ?? Number.MAX_SAFE_INTEGER)
  ))
}
