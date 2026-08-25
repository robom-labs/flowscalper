// Backend Strategy Registry 순서와 비전문가용 표시 문구를 공유한다.
import type { StrategyRow } from './types'

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
  OFI_RETURN_CONFLUENCE_V1: 'OFI·가격동행 · OFI·단기수익률 동행',
  BOOK_SLOPE_ASYMMETRY_V1: '호가 기울기 · 10단계 호가 비대칭',
}

export function strategyLabel(strategy: StrategyRow | undefined, strategyId: string) {
  return strategy ? `${strategy.short_name} · ${strategy.display_name_ko}` : fallbackLabels[strategyId] ?? strategyId
}

export function orderedStrategies(strategies: StrategyRow[]) {
  return [...strategies]
}

export function strategyWaitReasonLabel(code: string) {
  if (code.includes('DATA') || code.includes('WARMUP')) return '데이터 준비 중'
  if (code.includes('REGIME') || code.includes('DIRECTION')) return '시장 방향 대기'
  if (code.includes('FLOW') || code.includes('AGGRESSOR')) return '체결 흐름 대기'
  if (code.includes('OFI')) return '주문 흐름 대기'
  if (code.includes('LIQUIDITY') || code.includes('DEPTH') || code.includes('BOOK') || code.includes('QUEUE') || code.includes('REFILL')) return '호가 조건 대기'
  if (code.includes('PERSISTENT') || code.includes('DURATION')) return '조건 지속 확인 중'
  if (code.includes('PRICE') || code.includes('VWAP') || code.includes('PULLBACK') || code.includes('SWEEP') || code.includes('STRUCTURE') || code.includes('RETRACE') || code.includes('COMPRESSED')) return '가격 구조 대기'
  return '세부 조건 대기'
}
