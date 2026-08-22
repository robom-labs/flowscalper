// 실제 캔들·호가·불변 거래계획과 PAPER 체결 마커를 Lightweight Charts로 표시한다.
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'
import { formatChartKstTime } from '../time'
import type { ChartData, CurrentPosition, HistoryRow } from '../types'

type Props = {
  chart: ChartData
  position?: CurrentPosition | null
  history?: HistoryRow[]
  replay?: boolean
}

type QuotePoint = { time: UTCTimestamp; value: number }
type LineApi = ISeriesApi<'Line', Time>
type CandleApi = ISeriesApi<'Candlestick', Time>
type MarkerController = { setMarkers: (markers: SeriesMarker<Time>[]) => void }

function quoteSeries(points: ChartData['points'], key: 'bid' | 'ask' | 'mid' | 'microprice') {
  const bySecond = new Map<number, QuotePoint>()
  for (const point of points) {
    const time = Math.floor(point.ts_ms / 1_000) as UTCTimestamp
    bySecond.set(time, { time, value: point[key] })
  }
  return [...bySecond.values()].sort((left, right) => Number(left.time) - Number(right.time))
}

function closestTime(times: UTCTimestamp[], tsMs: number) {
  if (times.length === 0) return undefined
  const target = Math.floor(tsMs / 1_000)
  return times.reduce((best, value) =>
    Math.abs(Number(value) - target) < Math.abs(Number(best) - target) ? value : best,
  )
}

function addPlanLine(
  series: CandleApi | LineApi,
  value: number | null | undefined,
  title: string,
  color: string,
) {
  if (value === null || value === undefined || !Number.isFinite(value)) return undefined
  return series.createPriceLine({
    price: value,
    color,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    axisLabelVisible: true,
    title,
  })
}

export function PriceChart({ chart, position = null, history = [], replay = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartApiRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<CandleApi | null>(null)
  const bidSeriesRef = useRef<LineApi | null>(null)
  const askSeriesRef = useRef<LineApi | null>(null)
  const microSeriesRef = useRef<LineApi | null>(null)
  const candleMarkersRef = useRef<MarkerController | null>(null)
  const microMarkersRef = useRef<MarkerController | null>(null)
  const fittedSelectionRef = useRef('')
  const candleData = useMemo(
    () =>
      chart.candles
        .map((candle) => ({
          time: candle.time as UTCTimestamp,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        }))
        .sort((left, right) => Number(left.time) - Number(right.time)),
    [chart.candles],
  )
  const bidData = useMemo(() => quoteSeries(chart.points, 'bid'), [chart.points])
  const askData = useMemo(() => quoteSeries(chart.points, 'ask'), [chart.points])
  const microData = useMemo(() => quoteSeries(chart.points, 'microprice'), [chart.points])
  const hasData = candleData.length > 0 || chart.points.length > 0

  useEffect(() => {
    const container = containerRef.current
    if (!container || !hasData) return

    const lightweightChart = createChart(container, {
      width: Math.max(1, container.clientWidth),
      height: Math.max(280, container.clientHeight),
      layout: {
        background: { type: ColorType.Solid, color: '#071219' },
        textColor: '#7893a2',
        fontFamily: 'Inter, Pretendard, ui-sans-serif, system-ui, sans-serif',
        attributionLogo: false,
      },
      localization: {
        locale: 'ko-KR',
        timeFormatter: formatChartKstTime,
      },
      grid: {
        vertLines: { color: '#142a35' },
        horzLines: { color: '#142a35' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1b3441', minimumWidth: 72 },
      timeScale: {
        borderColor: '#1b3441',
        timeVisible: true,
        secondsVisible: true,
        rightOffset: 3,
        tickMarkFormatter: formatChartKstTime,
      },
      handleScroll: true,
      handleScale: true,
    })

    const candleSeries = lightweightChart.addSeries(CandlestickSeries, {
      upColor: '#64d9be',
      downColor: '#ff7e87',
      borderVisible: false,
      wickUpColor: '#64d9be',
      wickDownColor: '#ff7e87',
      priceLineVisible: false,
    })
    const bidSeries = lightweightChart.addSeries(LineSeries, {
      color: '#5998c6',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const askSeries = lightweightChart.addSeries(LineSeries, {
      color: '#c5767d',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const microSeries = lightweightChart.addSeries(LineSeries, {
      color: '#d9e8ee',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })

    chartApiRef.current = lightweightChart
    candleSeriesRef.current = candleSeries
    bidSeriesRef.current = bidSeries
    askSeriesRef.current = askSeries
    microSeriesRef.current = microSeries
    candleMarkersRef.current = createSeriesMarkers(candleSeries, [])
    microMarkersRef.current = createSeriesMarkers(microSeries, [])

    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      lightweightChart.applyOptions({
        width: Math.max(1, Math.floor(entry.contentRect.width)),
        height: Math.max(280, Math.floor(entry.contentRect.height)),
      })
    })
    resizeObserver.observe(container)
    return () => {
      resizeObserver.disconnect()
      chartApiRef.current = null
      candleSeriesRef.current = null
      bidSeriesRef.current = null
      askSeriesRef.current = null
      microSeriesRef.current = null
      candleMarkersRef.current = null
      microMarkersRef.current = null
      lightweightChart.remove()
    }
  }, [hasData])

  useEffect(() => {
    const chartApi = chartApiRef.current
    const candleSeries = candleSeriesRef.current
    const bidSeries = bidSeriesRef.current
    const askSeries = askSeriesRef.current
    const microSeries = microSeriesRef.current
    if (!chartApi || !candleSeries || !bidSeries || !askSeries || !microSeries) return

    candleSeries.setData(candleData)
    bidSeries.setData(bidData)
    askSeries.setData(askData)
    microSeries.setData(microData)
    bidSeries.applyOptions({ lineVisible: bidData.length > 0 })
    askSeries.applyOptions({ lineVisible: askData.length > 0 })
    microSeries.applyOptions({ lineVisible: microData.length > 0 })
    chartApi.timeScale().applyOptions({ secondsVisible: chart.interval.endsWith('s') })

    const selection = `${chart.symbol}:${chart.interval}`
    if (
      fittedSelectionRef.current !== selection
      && (candleData.length > 0 || microData.length > 0)
    ) {
      chartApi.timeScale().fitContent()
      fittedSelectionRef.current = selection
    }
  }, [askData, bidData, candleData, chart.interval, chart.symbol, microData])

  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const microSeries = microSeriesRef.current
    if (!candleSeries || !microSeries) return
    const anchorSeries: CandleApi | LineApi = candleData.length > 0 ? candleSeries : microSeries
    const lines = [
      addPlanLine(anchorSeries, chart.lines.entry, '진입', '#64d9be'),
      addPlanLine(anchorSeries, chart.lines.take_profit, 'TP1', '#69bff8'),
      addPlanLine(anchorSeries, chart.lines.take_profit_2, 'TP2', '#4be0ea'),
      addPlanLine(anchorSeries, chart.lines.stop, 'SL', '#ff7e87'),
    ].filter((line) => line !== undefined)
    return () => {
      for (const line of lines) anchorSeries.removePriceLine(line)
    }
  }, [
    candleData.length,
    chart.lines.entry,
    chart.lines.stop,
    chart.lines.take_profit,
    chart.lines.take_profit_2,
  ])

  useEffect(() => {
    const candleMarkers = candleMarkersRef.current
    const microMarkers = microMarkersRef.current
    if (!candleMarkers || !microMarkers) return
    const availableTimes = (
      candleData.length > 0 ? candleData.map((item) => item.time) : microData.map((item) => item.time)
    ) as UTCTimestamp[]
    const markers: SeriesMarker<Time>[] = []
    if (position?.symbol === chart.symbol) {
      const time = closestTime(availableTimes, position.signal_time)
      if (time !== undefined) {
        markers.push({
          time,
          position: position.side === 'LONG' ? 'belowBar' : 'aboveBar',
          color: '#64d9be',
          shape: position.side === 'LONG' ? 'arrowUp' : 'arrowDown',
          text: `PAPER 체결 ${position.actual_entry}`,
        })
      }
    }
    for (const trade of history.filter((item) => item.symbol === chart.symbol).slice(0, 8)) {
      const entryTime = closestTime(availableTimes, trade.entry_ts_ms)
      const exitTime = closestTime(availableTimes, trade.exit_ts_ms)
      if (entryTime !== undefined) {
        markers.push({
          time: entryTime,
          position: trade.side === 'LONG' ? 'belowBar' : 'aboveBar',
          color: '#64d9be',
          shape: trade.side === 'LONG' ? 'arrowUp' : 'arrowDown',
          text: `진입 ${trade.entry}`,
        })
      }
      if (exitTime !== undefined) {
        markers.push({
          time: exitTime,
          position: trade.side === 'LONG' ? 'aboveBar' : 'belowBar',
          color: '#f1c96d',
          shape: 'circle',
          text: `종료 ${trade.exit} · ${trade.exit_reason}`,
        })
      }
    }
    markers.sort((left, right) => Number(left.time) - Number(right.time))
    candleMarkers.setMarkers(candleData.length > 0 ? markers : [])
    microMarkers.setMarkers(candleData.length > 0 ? [] : markers)
  }, [candleData, chart.symbol, history, microData, position])

  const plannedLevels = chart.lines.entry !== null

  return (
    <section className="panel chart-panel" aria-labelledby={replay ? 'replay-chart-title' : 'chart-title'}>
      <div className="panel-title">
        <div>
          <p className="section-kicker">{replay ? 'DETERMINISTIC REPLAY' : 'EXECUTABLE MICROSTRUCTURE'}</p>
          <h2 id={replay ? 'replay-chart-title' : 'chart-title'}>{chart.symbol} · {chart.interval}</h2>
        </div>
        <span className="fixture-note">{chart.fixture ? 'OFFLINE DEMO' : 'LIVE PUBLIC'} · KST</span>
      </div>
      <div
        ref={containerRef}
        className="chart-wrap"
        role="img"
        aria-label={`${chart.symbol} 실제 캔들·호가 차트${plannedLevels ? ', 진입 TP1 TP2 SL 선 포함' : ', 확정된 진입 계획 없음'}`}
      >
        {!hasData ? (
          <div className="chart-empty">
            <b>실제 거래 캔들을 기다리는 중입니다</b>
            <span>호가만으로 가짜 캔들을 만들지 않습니다.</span>
          </div>
        ) : null}
      </div>
      <div className="chart-legend" aria-label="차트 범례">
        <span className="candle-dot">{candleData.length > 0 ? '실제 캔들' : '실제 캔들 대기'}</span>
        <span className="bid-dot">bid</span>
        <span className="ask-dot">ask</span>
        <span className="micro-dot">microprice</span>
        <span>KST</span>
      </div>
    </section>
  )
}
