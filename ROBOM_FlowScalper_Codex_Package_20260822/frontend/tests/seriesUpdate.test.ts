// 실시간 최신 캔들은 update하고 선택·과거 변경만 reset하는 계약을 검증한다.
import { expect, test } from 'vitest'
import { seriesUpdateMode } from '../src/chart/seriesUpdate'
import type { IndicatorCandle } from '../src/chart/indicators'

const candle = (time: number, close = time): IndicatorCandle => ({ time, open: close, high: close, low: close, close, volume: 1 })

test('uses incremental update for the latest candle or one appended candle', () => {
  const previous = [candle(1), candle(2)]
  expect(seriesUpdateMode('BTC:1s', 'BTC:1s', previous, [candle(1), candle(2, 2.5)])).toBe('UPDATE')
  expect(seriesUpdateMode('BTC:1s', 'BTC:1s', previous, [...previous, candle(3)])).toBe('UPDATE')
  expect(seriesUpdateMode('BTC:1s', 'BTC:1s', previous, previous)).toBe('NOOP')
})

test('resets only for selection, truncation, gap or historical mutation', () => {
  const previous = [candle(1), candle(2), candle(3)]
  expect(seriesUpdateMode('BTC:1s', 'ETH:1s', previous, previous)).toBe('RESET')
  expect(seriesUpdateMode('BTC:1s', 'BTC:1s', previous, previous.slice(0, 2))).toBe('RESET')
  expect(seriesUpdateMode('BTC:1s', 'BTC:1s', previous, [...previous, candle(4), candle(5)])).toBe('RESET')
  expect(seriesUpdateMode('BTC:1s', 'BTC:1s', previous, [candle(1, 99), candle(2), candle(3)])).toBe('RESET')
})
