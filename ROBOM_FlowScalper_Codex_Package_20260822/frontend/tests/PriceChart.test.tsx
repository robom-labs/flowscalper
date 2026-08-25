// lightweight-charts mock으로 차트 인스턴스 재사용·증분 update·지표 접근성을 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

const chartMocks = vi.hoisted(() => ({
  createChart: vi.fn(),
  createSeriesMarkers: vi.fn(),
  removePane: vi.fn(),
  setVisibleLogicalRange: vi.fn(),
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
  chartMocks.removePane.mockReset()
  chartMocks.setVisibleLogicalRange.mockReset()
  chartMocks.createSeriesMarkers.mockImplementation(() => ({ setMarkers: vi.fn() }))
  chartMocks.createChart.mockImplementation(() => {
    const panes: { setHeight: ReturnType<typeof vi.fn>; paneIndex: () => number; getSeries: () => typeof chartMocks.series; priceScale: () => { applyOptions: ReturnType<typeof vi.fn> } }[] = []
    const pane = () => {
      const current = {
        setHeight: vi.fn(),
        paneIndex: () => panes.indexOf(current),
        getSeries: () => chartMocks.series.filter((series) => series.pane === panes.indexOf(current)),
        priceScale: () => ({ applyOptions: vi.fn() }),
      }
      return current
    }
    const value = pane()
    panes.push(value)
    const timeScale = {
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      fitContent: vi.fn(),
      setVisibleLogicalRange: chartMocks.setVisibleLogicalRange,
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
      addPane: vi.fn(() => { const next = pane(); panes.push(next); return next }),
      removePane: vi.fn((index: number) => { chartMocks.removePane(index); panes.splice(index, 1) }),
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
  expect(chartMocks.series.some((series) => series.pane === 1)).toBe(false)
})

test('exposes MA and lower-pane indicator toggles with aria-pressed', async () => {
  render(<PriceChart chart={chart()} />)
  await waitFor(() => expect(chartMocks.createChart).toHaveBeenCalled())
  expect(screen.getByRole('button', { name: 'MA5' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByRole('button', { name: 'MA10' })).toHaveAttribute('aria-pressed', 'true')
  for (const label of ['MA5', 'EMA20', 'VWAP', '볼린저', 'RSI', 'MACD']) {
    const button = screen.getByRole('button', { name: label })
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-pressed', 'true')
  }
  expect(chartMocks.series.some((series) => series.pane > 0)).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'RSI' }))
  expect(chartMocks.removePane).toHaveBeenCalled()
})

test('shows only the latest 120 candles on the first 200-candle load', async () => {
  render(<PriceChart chart={chart('BTCUSDT', 200)} />)
  await waitFor(() => expect(chartMocks.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 80, to: 199 }))
})

test('shows the current PAPER direction and protection prices directly on the chart', async () => {
  render(<PriceChart chart={chart()} activePositionCount={2} overlay={{
    key: 'position-1',
    label: '호가 쏠림 순간추세 · BASE',
    symbol: 'BTCUSDT',
    side: 'LONG',
    signalTime: 20_000,
    entry: 100,
    tp1: 101,
    tp2: 102,
    stop: 99,
  }} />)

  await waitFor(() => expect(chartMocks.createChart).toHaveBeenCalled())
  const banner = screen.getByLabelText('현재 PAPER 진입')
  expect(banner).toHaveTextContent('PAPER 진입 중 · 상승')
  expect(banner).toHaveTextContent('같은 종목 외 1건')
  expect(banner).toHaveTextContent('진입 100')
  expect(banner).toHaveTextContent('TP1 101')
  expect(banner).toHaveTextContent('SL 99')
})
