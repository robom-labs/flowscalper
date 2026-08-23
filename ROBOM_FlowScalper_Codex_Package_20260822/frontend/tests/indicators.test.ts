// 전문 차트 지표가 결정적 수식과 짧은 입력 경계를 지키는지 검증한다.
import { describe, expect, test } from 'vitest'
import { bollinger, ema, macd, rsi, sma, vwap, type IndicatorCandle } from '../src/chart/indicators'

const candles = (closes: number[]): IndicatorCandle[] => closes.map((close, index) => ({
  time: index + 1,
  open: close - 0.5,
  high: close + 1,
  low: close - 1,
  close,
  volume: index + 1,
}))

describe('price indicators', () => {
  test('calculates SMA, seeded EMA, VWAP and population Bollinger bands', () => {
    const input = candles([1, 2, 3, 4, 5])
    const original = structuredClone(input)
    expect(sma(input, 3)).toEqual([
      { time: 3, value: 2 },
      { time: 4, value: 3 },
      { time: 5, value: 4 },
    ])
    expect(ema(input, 3).map((point) => point.value)).toEqual([2, 3, 4])
    expect(vwap(input)[0]).toEqual({ time: 1, value: 1 })
    const bands = bollinger(input, 5, 2)
    expect(bands.middle[0].value).toBe(3)
    expect(bands.upper[0].value).toBeCloseTo(5.8284271247)
    expect(bands.lower[0].value).toBeCloseTo(0.1715728753)
    expect(input).toEqual(original)
  })

  test('uses Wilder RSI boundaries and deterministic MACD warmup', () => {
    expect(rsi(candles([5, 5, 5, 5]), 3)[0].value).toBe(50)
    expect(rsi(candles([1, 2, 3, 4]), 3)[0].value).toBe(100)
    expect(rsi(candles([4, 3, 2, 1]), 3)[0].value).toBe(0)
    const result = macd(candles(Array.from({ length: 40 }, (_, index) => index + 1)))
    expect(result.line[0].time).toBe(26)
    expect(result.signal[0].time).toBe(34)
    expect(result.histogram[0].time).toBe(34)
  })

  test('returns empty output when the input cannot warm up', () => {
    const input = candles([1, 2])
    expect(sma(input, 5)).toEqual([])
    expect(ema(input, 5)).toEqual([])
    expect(rsi(input, 14)).toEqual([])
    expect(macd(input).line).toEqual([])
  })
})
