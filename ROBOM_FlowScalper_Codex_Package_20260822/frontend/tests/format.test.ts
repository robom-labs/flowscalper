// 화면 숫자와 종료 사유가 과도한 소수점 없이 일관되게 보이는지 검증한다.
import { describe, expect, test } from 'vitest'
import {
  costProfileLabel,
  exitReasonLabel,
  formatCompactNumber,
  formatDurationMs,
  formatPercentFraction,
  formatPrice,
  formatQuantity,
  formatUsdt,
  paperAccountLabel,
  priceFractionDigits,
  sampleTypeLabel,
  sideLabel,
} from '../src/format'

describe('beginner-facing number formatting', () => {
  test('keeps useful money precision without exposing ledger tails', () => {
    expect(formatUsdt('999.56592713608', { equity: true })).toBe('999.57 USDT')
    expect(formatUsdt('-0.1235724000001', { signed: true })).toBe('-0.1236 USDT')
    expect(formatUsdt('1.4788000000', { signed: true })).toBe('+1.479 USDT')
  })

  test('formats price, quantity, percentage, volume and short holding time', () => {
    expect(formatPrice('0.0000123456789')).toBe('0.00001235')
    expect(priceFractionDigits('0.061234')).toBe(6)
    expect(priceFractionDigits('134.10')).toBe(3)
    expect(formatQuantity('116.53000000')).toBe('116.53')
    expect(formatPercentFraction('0.60549')).toBe('60.5%')
    expect(formatDurationMs(1_696)).toBe('1.7초')
    expect(formatDurationMs(3_600_000)).toBe('1시간')
    expect(formatDurationMs(129_600_000)).toBe('36시간')
    expect(formatCompactNumber(12_345_678)).toBe('1,234.6만')
  })

  test('translates known exit reasons for non-experts', () => {
    expect(exitReasonLabel('EDGE_DECAY')).toBe('가격·근거 동시 악화')
    expect(exitReasonLabel('TP2')).toBe('2차 익절')
    expect(sideLabel('LONG')).toBe('상승 방향')
    expect(costProfileLabel('STRESS')).toBe('보수 비용')
    expect(sampleTypeLabel('LIVE_PUBLIC')).toBe('공개시장 모의거래')
  })

  test('distinguishes shared and independent PAPER accounts', () => {
    expect(paperAccountLabel('SHARED_PAPER')).toBe('공동계좌')
    expect(paperAccountLabel('LSA_REVERSAL_V1:BASE')).toBe('전략 독립계좌')
    expect(paperAccountLabel('REPLAY')).toBe('저장 재생')
  })
})
