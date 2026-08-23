// lightweight-charts mock으로 차트 인스턴스 재사용·증분 update·지표 접근성을 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

const chartMocks = vi.hoisted(() => ({
  createChart: vi.fn(),
  createSeriesMarkers: vi.fn(),
  series: [] as {
    kind: string
    pane: number
    setData: ReturnType<typeof vi.fn>
    update: ReturnType<typeof vi.fn>
    applyOptions: ReturnType<typeof vi.fn>
    createPriceLine: ReturnType<typeof vi.fn>
    removePriceLine: ReturnType<typeof vi.fn>
  }[],
}))

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'CandlestickSeries',
  HistogramSeries: 'HistogramSeries',
  LineSeries: 'LineSeries',
  ColorType: { Solid: 'Solid' },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Dashed: 2 },
  createChart: chartMocks.createChart,
  createSeriesMarkers: chartMocks.createSeriesMarkers,
}))

import { PriceChart } from '../src/components/PriceChart'
import type { ChartData } from '../src/types'

function candle(time: number, close = 100 + time / 100) {
  return { time, open_ts_ms: time * 1000, open: close - 0.1, high: close + 0.2, low: close - 0.2, close, volume: time, trade_count: 1 }
}

function chart(symbol = 'BTCUSDT', count = 30): ChartData {
  return {
    symbol,
    interval: '1s',
    points: [],
    candles: Array.from({ length: count }, (_, index) => candle(index + 1)),
    lines: { entry: null, take_profit: null, take_profit_2: null, stop: null },
    fixture: false,
  }
}

beforeEach(() => {
  chartMocks.series.length = 0
  chartMocks.createSeriesMarkers.mockImplementation(() => ({ setMarkers: vi.fn() }))
  chartMocks.createChart.mockImplementation(() => {
    const panes = Array.from({ length: 4 }, () => ({ setHeight: vi.fn() }))
    const timeScale = {
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      fitContent: vi.fn(),
      applyOptions: vi.fn(),
      scrollToRealTime: vi.fn(),
      scrollPosition: vi.fn(() => 0),
    }
    return {
      addSeries: vi.fn((kind: string, _options: object, pane = 0) => {
        const priceLine = { applyOptions: vi.fn() }
        const series = {
          kind,
          pane,
          setData: vi.fn(),
          update: vi.fn(),
          applyOptions: vi.fn(),
          createPriceLine: vi.fn(() => priceLine),
          removePriceLine: vi.fn(),
        }
        chartMocks.series.push(series)
        return series
      }),
      timeScale: () => timeScale,
      subscribeCrosshairMove: vi.fn(),
      unsubscribeCrosshairMove: vi.fn(),
      applyOptions: vi.fn(),
      panes: () => panes,
      remove: vi.fn(),
    }
  })
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1 })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const setDataCount = () => chartMocks.series.reduce((total, series) => total + series.setData.mock.calls.length, 0)
const updateCount = () => chartMocks.series.reduce((total, series) => total + series.update.mock.calls.length, 0)

test('reuses one chart and updates the newest candle without full setData', async () => {
  const first = chart()
  const { rerender } = render(<PriceChart chart={first} />)
  await waitFor(() => expect(chartMocks.createChart).toHaveBeenCalledTimes(1))
  const initialSetData = setDataCount()
  expect(initialSetData).toBeGreaterThan(0)

  rerender(<PriceChart chart={{ ...first, candles: [...first.candles, candle(31)] }} />)
  await waitFor(() => expect(updateCount()).toBeGreaterThan(0))
  expect(chartMocks.createChart).toHaveBeenCalledTimes(1)
  expect(setDataCount()).toBe(initialSetData)

  rerender(<PriceChart chart={chart('ETHUSDT')} />)
  await waitFor(() => expect(setDataCount()).toBeGreaterThan(initialSetData))
  expect(chartMocks.createChart).toHaveBeenCalledTimes(1)
  expect(chartMocks.series.some((series) => series.pane === 2)).toBe(true)
  expect(chartMocks.series.some((series) => series.pane === 3)).toBe(true)
})

test('exposes MA and lower-pane indicator toggles with aria-pressed', async () => {
  render(<PriceChart chart={chart()} />)
  await waitFor(() => expect(chartMocks.createChart).toHaveBeenCalled())
  expect(screen.getByRole('button', { name: 'MA5' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'MA10' })).toHaveAttribute('aria-pressed', 'false')
  for (const label of ['MA10', 'EMA20', 'VWAP', '볼린저', 'RSI', 'MACD']) {
    const button = screen.getByRole('button', { name: label })
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-pressed', 'true')
  }
})
