// 전문 PAPER 차트를 한 번 생성하고 실시간 데이터는 series.update로 증분 반영한다.
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type AutoscaleInfoProvider,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { bollinger, ema, macd, rsi, sma, vwap, type IndicatorCandle, type IndicatorPoint } from '../chart/indicators'
import { seriesUpdateMode } from '../chart/seriesUpdate'
import { formatChartKstTime, formatKstDateTime } from '../time'
import { formatCompactNumber, formatPrice } from '../format'
import type { ChartData, HistoryRow } from '../types'

export type ChartOverlay = {
  key: string
  label: string
  symbol: string
  side: 'LONG' | 'SHORT'
  signalTime: number
  entry: number
  tp1: number
  tp2: number | null
  stop: number
  initialStop?: number
  currentStop?: number
}

type Props = { chart: ChartData; overlay?: ChartOverlay | null; history?: HistoryRow[]; replay?: boolean; compact?: boolean }
type QuotePoint = { time: UTCTimestamp; value: number }
type LineApi = ISeriesApi<'Line', Time>
type CandleApi = ISeriesApi<'Candlestick', Time>
type HistogramApi = ISeriesApi<'Histogram', Time>
type MarkerController = { setMarkers: (markers: SeriesMarker<Time>[]) => void }
type PriceLineSlot = { api: IPriceLine; series: CandleApi | LineApi; value: number }
type IndicatorKey = 'MA5' | 'MA10' | 'MA20' | 'MA60' | 'EMA20' | 'VWAP' | 'BOLLINGER' | 'BID' | 'ASK' | 'MICROPRICE' | 'RSI' | 'MACD'

const colors: Record<IndicatorKey, string> = {
  MA5: '#f1c96d', MA10: '#69bff8', MA20: '#d997ff', MA60: '#ff9f6e', EMA20: '#60dfa9', VWAP: '#f39bd5', BOLLINGER: '#9c8df0', BID: '#5998c6', ASK: '#c5767d', MICROPRICE: '#d9e8ee', RSI: '#f1c96d', MACD: '#64d9be',
}

const defaultVisible: Record<IndicatorKey, boolean> = {
  MA5: false, MA10: true, MA20: true, MA60: false, EMA20: false, VWAP: false, BOLLINGER: false, BID: false, ASK: false, MICROPRICE: false, RSI: false, MACD: false,
}

function initialIndicators() {
  try {
    const saved = JSON.parse(globalThis.localStorage?.getItem('robom.market.indicators.v1') ?? '{}') as Partial<Record<IndicatorKey, unknown>>
    return Object.fromEntries(Object.entries(defaultVisible).map(([key, value]) => [key, typeof saved[key as IndicatorKey] === 'boolean' ? saved[key as IndicatorKey] : value])) as Record<IndicatorKey, boolean>
  } catch {
    return defaultVisible
  }
}

function quoteSeries(points: ChartData['points'], key: 'bid' | 'ask' | 'microprice') {
  const bySecond = new Map<number, QuotePoint>()
  for (const point of points) bySecond.set(Math.floor(point.ts_ms / 1_000), { time: Math.floor(point.ts_ms / 1_000) as UTCTimestamp, value: point[key] })
  return [...bySecond.values()].sort((left, right) => Number(left.time) - Number(right.time))
}

function chartPoints(points: IndicatorPoint[]): QuotePoint[] {
  return points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value }))
}

function closestTime(times: UTCTimestamp[], tsMs: number) {
  if (times.length === 0) return undefined
  const target = Math.floor(tsMs / 1_000)
  return times.reduce((best, value) => Math.abs(Number(value) - target) < Math.abs(Number(best) - target) ? value : best)
}

function last<T>(values: T[]) {
  return values.at(-1)
}

export const PriceChart = memo(function PriceChart({ chart, overlay = null, history = [], replay = false, compact = false }: Props) {
  const [visible, setVisible] = useState(initialIndicators)
  const [showReturn, setShowReturn] = useState(false)
  const [fullWindow, setFullWindow] = useState(false)
  const panelRef = useRef<HTMLElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartApiRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<CandleApi | null>(null)
  const volumeRef = useRef<HistogramApi | null>(null)
  const lineRefs = useRef<Record<string, LineApi>>({})
  const histogramRefs = useRef<Record<string, HistogramApi>>({})
  const candleMarkersRef = useRef<MarkerController | null>(null)
  const microMarkersRef = useRef<MarkerController | null>(null)
  const previousCandlesRef = useRef<IndicatorCandle[]>([])
  const previousSelectionRef = useRef('')
  const priceLinesRef = useRef<Record<string, PriceLineSlot>>({})
  const markerSignatureRef = useRef('')
  const tooltipFrameRef = useRef(0)
  const visibleRef = useRef(visible)
  const studyPanesRef = useRef<Record<'RSI' | 'MACD', number | null>>({ RSI: null, MACD: null })

  const candles = useMemo<IndicatorCandle[]>(() => chart.candles.map((candle) => ({ time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close, volume: candle.volume })).sort((left, right) => left.time - right.time), [chart.candles])
  const candleData = useMemo(() => candles.map((candle) => ({ time: candle.time as UTCTimestamp, open: candle.open, high: candle.high, low: candle.low, close: candle.close })), [candles])
  const volumeData = useMemo(() => candles.map((candle) => ({ time: candle.time as UTCTimestamp, value: candle.volume, color: candle.close >= candle.open ? '#64d9be55' : '#ff7e8755' })), [candles])
  const bidData = useMemo(() => quoteSeries(chart.points, 'bid'), [chart.points])
  const askData = useMemo(() => quoteSeries(chart.points, 'ask'), [chart.points])
  const microData = useMemo(() => quoteSeries(chart.points, 'microprice'), [chart.points])
  const indicatorData = useMemo(() => {
    const bands = bollinger(candles)
    const momentum = macd(candles)
    return {
      MA5: chartPoints(sma(candles, 5)), MA10: chartPoints(sma(candles, 10)), MA20: chartPoints(sma(candles, 20)), MA60: chartPoints(sma(candles, 60)), EMA20: chartPoints(ema(candles, 20)), VWAP: chartPoints(vwap(candles)), BOLLINGER_MIDDLE: chartPoints(bands.middle), BOLLINGER_UPPER: chartPoints(bands.upper), BOLLINGER_LOWER: chartPoints(bands.lower), RSI: chartPoints(rsi(candles)), MACD: chartPoints(momentum.line), MACD_SIGNAL: chartPoints(momentum.signal), MACD_HISTOGRAM: momentum.histogram.map((point) => ({ time: point.time as UTCTimestamp, value: point.value, color: point.value >= 0 ? '#64d9be88' : '#ff7e8788' })),
    }
  }, [candles])
  const hasData = candles.length > 0 || chart.points.length > 0

  useEffect(() => {
    const container = containerRef.current
    if (!container || !hasData) return
    const api = createChart(container, {
      width: Math.max(1, container.clientWidth), height: Math.max(320, container.clientHeight),
      layout: { background: { type: ColorType.Solid, color: '#071219' }, textColor: '#7893a2', fontFamily: 'Inter, Pretendard, ui-sans-serif, system-ui, sans-serif', attributionLogo: false },
      localization: { locale: 'ko-KR', timeFormatter: formatChartKstTime },
      grid: { vertLines: { color: '#142a35' }, horzLines: { color: '#142a35' } },
      crosshair: { mode: CrosshairMode.Normal }, rightPriceScale: { borderColor: '#1b3441', minimumWidth: 72 },
      timeScale: { borderColor: '#1b3441', timeVisible: true, secondsVisible: true, rightOffset: 3, tickMarkFormatter: formatChartKstTime }, handleScroll: true, handleScale: true,
    })
    const candle = api.addSeries(CandlestickSeries, { upColor: '#64d9be', downColor: '#ff7e87', borderVisible: false, wickUpColor: '#64d9be', wickDownColor: '#ff7e87', priceLineVisible: false }, 0)
    const volume = api.addSeries(HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 0)
    const addLine = (name: string, color: string, pane = 0, lineWidth: 1 | 2 = 1) => {
      const series = api.addSeries(LineSeries, { color, lineWidth, priceLineVisible: false, lastValueVisible: false }, pane)
      lineRefs.current[name] = series
      return series
    }
    for (const key of ['MA5', 'MA10', 'MA20', 'MA60', 'EMA20', 'VWAP'] as const) addLine(key, colors[key], 0, 2)
    addLine('BOLLINGER_MIDDLE', colors.BOLLINGER, 0)
    addLine('BOLLINGER_UPPER', '#8075c8', 0)
    addLine('BOLLINGER_LOWER', '#8075c8', 0)
    addLine('BID', colors.BID)
    addLine('ASK', colors.ASK)
    const micro = addLine('MICROPRICE', colors.MICROPRICE)
    api.panes()[0]?.priceScale('volume').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } })
    chartApiRef.current = api
    candleRef.current = candle
    volumeRef.current = volume
    candleMarkersRef.current = createSeriesMarkers(candle, [])
    microMarkersRef.current = createSeriesMarkers(micro, [])
    const visibleRangeHandler = () => {
      const position = api.timeScale().scrollPosition()
      setShowReturn(position < -0.5)
    }
    api.timeScale().subscribeVisibleLogicalRangeChange(visibleRangeHandler)
    const crosshairHandler = (param: { point?: { x: number; y: number }; time?: Time; seriesData: Map<unknown, unknown> }) => {
      window.cancelAnimationFrame(tooltipFrameRef.current)
      tooltipFrameRef.current = window.requestAnimationFrame(() => {
        const tooltip = tooltipRef.current
        if (!tooltip || !param.point || param.time === undefined) {
          if (tooltip) tooltip.hidden = true
          return
        }
        const candleValue = param.seriesData.get(candle) as Record<string, number> | undefined
        const volumeValue = param.seriesData.get(volume) as Record<string, number> | undefined
        const indicatorValues = Object.entries(lineRefs.current).flatMap(([name, series]) => {
          if (!visibleRef.current[name as IndicatorKey] && !['BOLLINGER_MIDDLE', 'BOLLINGER_UPPER', 'BOLLINGER_LOWER', 'RSI70', 'RSI30', 'MACD_SIGNAL'].includes(name)) return []
          const value = param.seriesData.get(series) as { value?: number } | undefined
          return value?.value === undefined ? [] : [`${name} ${value.value.toFixed(4)}`]
        })
        tooltip.textContent = [formatKstDateTime(Number(param.time) * 1000), candleValue ? `시 ${formatPrice(candleValue.open)} · 고 ${formatPrice(candleValue.high)} · 저 ${formatPrice(candleValue.low)} · 종 ${formatPrice(candleValue.close)}` : '', volumeValue?.value !== undefined ? `거래량 ${formatCompactNumber(volumeValue.value)}` : '', ...indicatorValues].filter(Boolean).join('\n')
        tooltip.hidden = false
        const width = tooltip.offsetWidth
        const left = Math.min(Math.max(8, param.point.x + 14), Math.max(8, container.clientWidth - width - 8))
        tooltip.style.transform = `translate(${left}px, ${Math.max(8, param.point.y - 28)}px)`
      })
    }
    api.subscribeCrosshairMove(crosshairHandler)
    let resizeFrame = 0
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return
      window.cancelAnimationFrame(resizeFrame)
      resizeFrame = window.requestAnimationFrame(() => api.applyOptions({ width: Math.max(1, Math.floor(entry.contentRect.width)), height: Math.max(320, Math.floor(entry.contentRect.height)) }))
    })
    observer.observe(container)
    return () => {
      window.cancelAnimationFrame(resizeFrame)
      window.cancelAnimationFrame(tooltipFrameRef.current)
      observer.disconnect()
      api.timeScale().unsubscribeVisibleLogicalRangeChange(visibleRangeHandler)
      api.unsubscribeCrosshairMove(crosshairHandler)
      chartApiRef.current = null; candleRef.current = null; volumeRef.current = null; lineRefs.current = {}; histogramRefs.current = {}; studyPanesRef.current = { RSI: null, MACD: null }; candleMarkersRef.current = null; microMarkersRef.current = null; priceLinesRef.current = {}; previousCandlesRef.current = []; previousSelectionRef.current = ''; markerSignatureRef.current = ''
      api.remove()
    }
  }, [hasData])

  useEffect(() => {
    const api = chartApiRef.current
    const candle = candleRef.current
    const volume = volumeRef.current
    if (!api || !candle || !volume) return
    const selection = `${chart.symbol}:${chart.interval}`
    const mode = seriesUpdateMode(previousSelectionRef.current, selection, previousCandlesRef.current, candles)
    const seriesData: Record<string, QuotePoint[]> = { ...indicatorData, BID: bidData, ASK: askData, MICROPRICE: microData }
    if (mode === 'RESET') {
      candle.setData(candleData); volume.setData(volumeData)
      for (const [name, series] of Object.entries(lineRefs.current)) {
        if (name === 'RSI70' || name === 'RSI30') continue
        series.setData(seriesData[name] ?? [])
      }
      histogramRefs.current.MACD?.setData(indicatorData.MACD_HISTOGRAM)
      const rsiTimes = indicatorData.RSI
      lineRefs.current.RSI70?.setData(rsiTimes.map((point) => ({ time: point.time, value: 70 })))
      lineRefs.current.RSI30?.setData(rsiTimes.map((point) => ({ time: point.time, value: 30 })))
      if (candleData.length > 120) api.timeScale().setVisibleLogicalRange({ from: candleData.length - 120, to: candleData.length - 1 })
      else api.timeScale().fitContent()
      markerSignatureRef.current = ''
    } else if (mode === 'UPDATE') {
      const latestCandle = last(candleData); const latestVolume = last(volumeData)
      if (latestCandle) candle.update(latestCandle)
      if (latestVolume) volume.update(latestVolume)
      for (const [name, series] of Object.entries(lineRefs.current)) {
        if (name === 'RSI70' || name === 'RSI30' || name === 'BID' || name === 'ASK' || name === 'MICROPRICE') continue
        const point = last(seriesData[name] ?? [])
        if (point) series.update(point)
      }
      const histogram = last(indicatorData.MACD_HISTOGRAM)
      if (histogram) histogramRefs.current.MACD?.update(histogram)
      const latestRsi = last(indicatorData.RSI)
      if (latestRsi) {
        lineRefs.current.RSI70?.update({ time: latestRsi.time, value: 70 })
        lineRefs.current.RSI30?.update({ time: latestRsi.time, value: 30 })
      }
    }
    if (mode !== 'RESET') {
      for (const name of ['BID', 'ASK', 'MICROPRICE'] as const) {
        const point = last(seriesData[name])
        if (point) lineRefs.current[name]?.update(point)
      }
    }
    api.timeScale().applyOptions({ secondsVisible: chart.interval.endsWith('s') })
    previousSelectionRef.current = selection
    previousCandlesRef.current = candles.map((candleItem) => ({ ...candleItem }))
  }, [askData, bidData, candleData, candles, chart.interval, chart.symbol, indicatorData, microData, volumeData])

  useEffect(() => {
    visibleRef.current = visible
    const api = chartApiRef.current
    if (!api) return
    const addStudy = (study: 'RSI' | 'MACD') => {
      if (studyPanesRef.current[study] !== null) return
      const pane = api.addPane(true)
      const paneIndex = pane.paneIndex()
      studyPanesRef.current[study] = paneIndex
      if (study === 'RSI') {
        lineRefs.current.RSI = api.addSeries(LineSeries, { color: colors.RSI, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, paneIndex)
        lineRefs.current.RSI70 = api.addSeries(LineSeries, { color: '#8b6b42', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, paneIndex)
        lineRefs.current.RSI30 = api.addSeries(LineSeries, { color: '#426b8b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, paneIndex)
        lineRefs.current.RSI.setData(indicatorData.RSI)
        lineRefs.current.RSI70.setData(indicatorData.RSI.map((point) => ({ time: point.time, value: 70 })))
        lineRefs.current.RSI30.setData(indicatorData.RSI.map((point) => ({ time: point.time, value: 30 })))
        pane.setHeight(110)
      } else {
        lineRefs.current.MACD = api.addSeries(LineSeries, { color: colors.MACD, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, paneIndex)
        lineRefs.current.MACD_SIGNAL = api.addSeries(LineSeries, { color: '#f1c96d', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, paneIndex)
        histogramRefs.current.MACD = api.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, paneIndex)
        lineRefs.current.MACD.setData(indicatorData.MACD)
        lineRefs.current.MACD_SIGNAL.setData(indicatorData.MACD_SIGNAL)
        histogramRefs.current.MACD.setData(indicatorData.MACD_HISTOGRAM)
        pane.setHeight(120)
      }
    }
    const removeStudy = (study: 'RSI' | 'MACD') => {
      const series = lineRefs.current[study]
      if (!series) return
      const paneIndex = api.panes().findIndex((pane) => pane.getSeries().includes(series))
      if (paneIndex > 0) api.removePane(paneIndex)
      if (study === 'RSI') {
        delete lineRefs.current.RSI; delete lineRefs.current.RSI70; delete lineRefs.current.RSI30
      } else {
        delete lineRefs.current.MACD; delete lineRefs.current.MACD_SIGNAL; delete histogramRefs.current.MACD
      }
      studyPanesRef.current[study] = null
      const other: 'RSI' | 'MACD' = study === 'RSI' ? 'MACD' : 'RSI'
      const otherSeries = lineRefs.current[other]
      studyPanesRef.current[other] = otherSeries ? api.panes().findIndex((pane) => pane.getSeries().includes(otherSeries)) : null
    }
    if (visible.RSI) addStudy('RSI'); else removeStudy('RSI')
    if (visible.MACD) addStudy('MACD'); else removeStudy('MACD')
    for (const key of ['MA5', 'MA10', 'MA20', 'MA60', 'EMA20', 'VWAP', 'BID', 'ASK', 'MICROPRICE'] as const) lineRefs.current[key]?.applyOptions({ visible: visible[key] })
    for (const key of ['BOLLINGER_MIDDLE', 'BOLLINGER_UPPER', 'BOLLINGER_LOWER']) lineRefs.current[key]?.applyOptions({ visible: visible.BOLLINGER })
  }, [indicatorData, visible])

  useEffect(() => {
    try { globalThis.localStorage?.setItem('robom.market.indicators.v1', JSON.stringify(visible)) } catch { /* 저장이 막힌 브라우저에서는 현재 세션 상태만 유지한다. */ }
  }, [visible])

  const lineValues = useMemo(
    () => overlay ? { entry: overlay.entry, tp1: overlay.tp1, tp2: overlay.tp2, initialStop: overlay.initialStop ?? overlay.stop, currentStop: overlay.currentStop ?? overlay.stop } : { entry: chart.lines.entry, tp1: chart.lines.take_profit, tp2: chart.lines.take_profit_2 ?? null, initialStop: chart.lines.stop, currentStop: chart.lines.stop },
    [chart.lines.entry, chart.lines.stop, chart.lines.take_profit, chart.lines.take_profit_2, overlay],
  )
  useEffect(() => {
    const series = candleRef.current ?? lineRefs.current.MICROPRICE
    if (!series) return
    const planPrices = Object.values(lineValues).filter((value): value is number => value !== null && Number.isFinite(value))
    series.applyOptions({
      autoscaleInfoProvider: ((baseImplementation) => {
        const base = baseImplementation()
        if (!base?.priceRange || planPrices.length === 0) return base
        return {
          ...base,
          priceRange: {
            minValue: Math.min(base.priceRange.minValue, ...planPrices),
            maxValue: Math.max(base.priceRange.maxValue, ...planPrices),
          },
          margins: { above: 16, below: 16 },
        }
      }) satisfies AutoscaleInfoProvider,
    })
    const separateCurrentStop = lineValues.currentStop !== lineValues.initialStop
    const definitions = {
      entry: [lineValues.entry, '진입', '#69bff8', LineStyle.Solid],
      initialStop: [lineValues.initialStop, '초기 손절', '#ff7e87', LineStyle.Dashed],
      currentStop: [separateCurrentStop ? lineValues.currentStop : null, '현재 손절', '#f1c96d', LineStyle.Solid],
      tp1: [lineValues.tp1, 'TP1', '#7bd1ff', LineStyle.Dashed],
      tp2: [lineValues.tp2, 'TP2', '#64d9be', LineStyle.Dashed],
    } as const
    for (const [key, [value, title, color, lineStyle]] of Object.entries(definitions)) {
      const current = priceLinesRef.current[key]
      if (value === null || !Number.isFinite(value)) {
        if (current) current.series.removePriceLine(current.api)
        delete priceLinesRef.current[key]
      } else if (current?.series === series) {
        if (current.value !== value) { current.api.applyOptions({ price: value }); current.value = value }
      } else {
        if (current) current.series.removePriceLine(current.api)
        priceLinesRef.current[key] = { series, value, api: series.createPriceLine({ price: value, color, lineWidth: 1, lineStyle, axisLabelVisible: true, title }) }
      }
    }
  }, [lineValues])

  useEffect(() => {
    const candleMarkers = candleMarkersRef.current; const microMarkers = microMarkersRef.current
    if (!candleMarkers || !microMarkers) return
    const times = (candleData.length ? candleData.map((item) => item.time) : microData.map((item) => item.time)) as UTCTimestamp[]
    const relevantHistory = history.filter((trade) => trade.symbol === chart.symbol).slice(-20)
    const signature = JSON.stringify([chart.symbol, overlay?.key, times.at(-1), relevantHistory.map((trade) => [trade.trade_id, trade.entry_ts_ms, trade.exit_ts_ms])])
    if (signature === markerSignatureRef.current) return
    const markers: SeriesMarker<Time>[] = []
    if (overlay?.symbol === chart.symbol) {
      const time = closestTime(times, overlay.signalTime)
      if (time !== undefined) markers.push({ time, position: overlay.side === 'LONG' ? 'belowBar' : 'aboveBar', color: '#64d9be', shape: overlay.side === 'LONG' ? 'arrowUp' : 'arrowDown', text: `${overlay.label} 모의 진입` })
    }
    for (const trade of relevantHistory) {
      const entryTime = closestTime(times, trade.entry_ts_ms); const exitTime = closestTime(times, trade.exit_ts_ms)
      if (entryTime !== undefined) markers.push({ time: entryTime, position: trade.side === 'LONG' ? 'belowBar' : 'aboveBar', color: '#64d9be', shape: trade.side === 'LONG' ? 'arrowUp' : 'arrowDown', text: `모의 진입 ${trade.entry}` })
      if (exitTime !== undefined) markers.push({ time: exitTime, position: trade.side === 'LONG' ? 'aboveBar' : 'belowBar', color: '#f1c96d', shape: 'circle', text: `종료 ${trade.exit}` })
    }
    markers.sort((left, right) => Number(left.time) - Number(right.time))
    candleMarkers.setMarkers(candleData.length ? markers : []); microMarkers.setMarkers(candleData.length ? [] : markers); markerSignatureRef.current = signature
  }, [candleData, chart.symbol, history, microData, overlay])

  useEffect(() => {
    const onFullscreen = () => setFullWindow(Boolean(document.fullscreenElement))
    const onEscape = (event: KeyboardEvent) => { if (event.key === 'Escape' && fullWindow && !document.fullscreenElement) setFullWindow(false) }
    document.addEventListener('fullscreenchange', onFullscreen); document.addEventListener('keydown', onEscape)
    return () => { document.removeEventListener('fullscreenchange', onFullscreen); document.removeEventListener('keydown', onEscape) }
  }, [fullWindow])

  const toggle = (key: IndicatorKey) => setVisible((current) => ({ ...current, [key]: !current[key] }))
  const toggleFullscreen = useCallback(async () => {
    if (document.fullscreenElement) { await document.exitFullscreen(); return }
    if (fullWindow) { setFullWindow(false); return }
    setFullWindow(true)
    try { await panelRef.current?.requestFullscreen() } catch { /* CSS 전체화면을 유지한다. */ }
  }, [fullWindow])
  const latestCandle = candles.at(-1)
  const groups: { label: string; items: [IndicatorKey, string, string][] }[] = [
    { label: '가격', items: [['MA5', 'MA5', '최근 가격의 평균선'], ['MA10', 'MA10', '최근 가격의 평균선'], ['MA20', 'MA20', '최근 가격의 평균선'], ['MA60', 'MA60', '최근 가격의 평균선'], ['EMA20', 'EMA20', '최근 가격에 더 큰 비중을 둔 평균선'], ['VWAP', 'VWAP', '거래량을 반영한 평균가격'], ['BOLLINGER', '볼린저', '최근 변동폭 범위']] },
    { label: '흐름', items: [['BID', 'bid', '현재 매수 호가'], ['ASK', 'ask', '현재 매도 호가'], ['MICROPRICE', 'microprice', '호가 수량을 반영한 참고가격']] },
    { label: '하단 지표', items: [['RSI', 'RSI', '최근 상승·하락 힘'], ['MACD', 'MACD', '추세 변화 참고']] },
  ]
  return (
    <section ref={panelRef} className={`panel chart-panel${compact ? ' compact-chart' : ''}${fullWindow ? ' chart-full-window' : ''}`} aria-labelledby={replay ? 'replay-chart-title' : 'chart-title'}>
      <div className="panel-title chart-title-row"><div>{!compact ? <p className="section-kicker">{replay ? 'PAST PLAYBACK' : '시장 차트'}</p> : null}<h2 id={replay ? 'replay-chart-title' : 'chart-title'}>{chart.symbol} · {chart.interval}</h2></div><div className="chart-title-actions"><span className="fixture-note">{chart.fixture ? '샘플 · LIVE 아님' : '공개시장'} · 한국시간</span><details className="indicator-popover"><summary>지표</summary><div className="indicator-controls" aria-label="차트 보조지표 선택">{groups.map((group) => <div className="indicator-group" key={group.label}><span>{group.label}</span>{group.items.map(([key, label, help]) => <button type="button" key={key} className={visible[key] ? 'selected' : ''} aria-pressed={visible[key]} title={help} style={{ '--line-color': colors[key] } as CSSProperties} onClick={() => toggle(key)}>{label}</button>)}</div>)}</div><p className="indicator-notice">화면 표시만 바뀌며 전략 기준은 바뀌지 않습니다.</p></details><button type="button" className="secondary-button" onClick={() => void toggleFullscreen()}>{fullWindow ? '전체화면 닫기' : '전체화면'}</button></div></div>
      {latestCandle ? <dl className="chart-stats"><div><dt>현재</dt><dd>{formatPrice(latestCandle.close)}</dd></div><div><dt>시가</dt><dd>{formatPrice(latestCandle.open)}</dd></div><div><dt>고가</dt><dd>{formatPrice(latestCandle.high)}</dd></div><div><dt>저가</dt><dd>{formatPrice(latestCandle.low)}</dd></div><div><dt>거래량</dt><dd>{formatCompactNumber(latestCandle.volume)}</dd></div></dl> : null}
      <div ref={containerRef} className="chart-wrap" role="img" aria-label={`${chart.symbol} 실제 캔들·거래량·전문 보조지표 PAPER 차트`}>
        {!hasData ? <div className="chart-empty"><b>시장 캔들을 기다리고 있습니다.</b><span>실제 공개시장 데이터가 도착하면 자동으로 표시됩니다.</span></div> : null}
        <div ref={tooltipRef} className="chart-tooltip" hidden />
        {showReturn ? <button type="button" className="return-realtime" onClick={() => chartApiRef.current?.timeScale().scrollToRealTime()}>현재로 돌아가기</button> : null}
      </div>
      <div className="chart-legend"><span className="candle-dot">가격 캔들</span><span className="volume-dot">거래량</span>{Object.entries(visible).filter(([, shown]) => shown).map(([key]) => <span key={key} style={{ '--legend-color': colors[key as IndicatorKey] } as CSSProperties}>{key}</span>)}<span>한국시간</span></div>
    </section>
  )
})
