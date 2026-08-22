// 실제 캔들·거래량·선택형 이동평균선과 모의거래 계획을 안정된 크기의 차트로 표시한다.
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
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
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
type HistogramApi = ISeriesApi<'Histogram', Time>
type MarkerController = { setMarkers: (markers: SeriesMarker<Time>[]) => void }

const movingAveragePeriods = [5, 10, 20, 60] as const
const movingAverageColors: Record<number, string> = {
  5: '#f1c96d',
  10: '#69bff8',
  20: '#d997ff',
  60: '#ff9f6e',
}

function quoteSeries(points: ChartData['points'], key: 'bid' | 'ask' | 'mid' | 'microprice') {
  const bySecond = new Map<number, QuotePoint>()
  for (const point of points) {
    const time = Math.floor(point.ts_ms / 1_000) as UTCTimestamp
    bySecond.set(time, { time, value: point[key] })
  }
  return [...bySecond.values()].sort((left, right) => Number(left.time) - Number(right.time))
}

function movingAverage(candles: ChartData['candles'], period: number) {
  const sorted = [...candles].sort((left, right) => left.time - right.time)
  const values: QuotePoint[] = []
  let sum = 0
  for (let index = 0; index < sorted.length; index += 1) {
    sum += sorted[index].close
    if (index >= period) sum -= sorted[index - period].close
    if (index >= period - 1) {
      values.push({
        time: sorted[index].time as UTCTimestamp,
        value: sum / period,
      })
    }
  }
  return values
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
  const [visibleAverages, setVisibleAverages] = useState<number[]>([5, 10])
  const [showQuotes, setShowQuotes] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const chartApiRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<CandleApi | null>(null)
  const volumeSeriesRef = useRef<HistogramApi | null>(null)
  const bidSeriesRef = useRef<LineApi | null>(null)
  const askSeriesRef = useRef<LineApi | null>(null)
  const microSeriesRef = useRef<LineApi | null>(null)
  const averageSeriesRef = useRef<Record<number, LineApi>>({})
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
  const volumeData = useMemo(
    () =>
      [...chart.candles]
        .sort((left, right) => left.time - right.time)
        .map((candle) => ({
          time: candle.time as UTCTimestamp,
          value: candle.volume,
          color: candle.close >= candle.open ? '#64d9be55' : '#ff7e8755',
        })),
    [chart.candles],
  )
  const averageData = useMemo(
    () => Object.fromEntries(movingAveragePeriods.map((period) => [period, movingAverage(chart.candles, period)])),
    [chart.candles],
  )
  const bidData = useMemo(() => quoteSeries(chart.points, 'bid'), [chart.points])
  const askData = useMemo(() => quoteSeries(chart.points, 'ask'), [chart.points])
  const microData = useMemo(() => quoteSeries(chart.points, 'microprice'), [chart.points])
  const latestCandle = chart.candles.at(-1)
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
    const volumeSeries = lightweightChart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      priceLineVisible: false,
      lastValueVisible: false,
    })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    const averageSeries = Object.fromEntries(movingAveragePeriods.map((period) => [
      period,
      lightweightChart.addSeries(LineSeries, {
        color: movingAverageColors[period],
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        visible: period === 5 || period === 10,
      }),
    ])) as Record<number, LineApi>
    const bidSeries = lightweightChart.addSeries(LineSeries, {
      color: '#5998c6',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      visible: false,
    })
    const askSeries = lightweightChart.addSeries(LineSeries, {
      color: '#c5767d',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      visible: false,
    })
    const microSeries = lightweightChart.addSeries(LineSeries, {
      color: '#d9e8ee',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      visible: false,
    })

    chartApiRef.current = lightweightChart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries
    averageSeriesRef.current = averageSeries
    bidSeriesRef.current = bidSeries
    askSeriesRef.current = askSeries
    microSeriesRef.current = microSeries
    candleMarkersRef.current = createSeriesMarkers(candleSeries, [])
    microMarkersRef.current = createSeriesMarkers(microSeries, [])

    let resizeFrame = 0
    let lastWidth = 0
    let lastHeight = 0
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      const width = Math.max(1, Math.floor(entry.contentRect.width))
      const height = Math.max(280, Math.floor(entry.contentRect.height))
      if (width === lastWidth && height === lastHeight) return
      lastWidth = width
      lastHeight = height
      window.cancelAnimationFrame(resizeFrame)
      resizeFrame = window.requestAnimationFrame(() => lightweightChart.applyOptions({ width, height }))
    })
    resizeObserver.observe(container)
    return () => {
      window.cancelAnimationFrame(resizeFrame)
      resizeObserver.disconnect()
      chartApiRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      averageSeriesRef.current = {}
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
    const volumeSeries = volumeSeriesRef.current
    const bidSeries = bidSeriesRef.current
    const askSeries = askSeriesRef.current
    const microSeries = microSeriesRef.current
    if (!chartApi || !candleSeries || !volumeSeries || !bidSeries || !askSeries || !microSeries) return

    candleSeries.setData(candleData)
    volumeSeries.setData(volumeData)
    bidSeries.setData(bidData)
    askSeries.setData(askData)
    microSeries.setData(microData)
    for (const period of movingAveragePeriods) {
      averageSeriesRef.current[period]?.setData(averageData[period])
    }
    chartApi.timeScale().applyOptions({ secondsVisible: chart.interval.endsWith('s') })

    const selection = `${chart.symbol}:${chart.interval}`
    if (fittedSelectionRef.current !== selection && (candleData.length > 0 || microData.length > 0)) {
      chartApi.timeScale().fitContent()
      fittedSelectionRef.current = selection
    }
  }, [askData, averageData, bidData, candleData, chart.interval, chart.symbol, microData, volumeData])

  useEffect(() => {
    for (const period of movingAveragePeriods) {
      averageSeriesRef.current[period]?.applyOptions({ visible: visibleAverages.includes(period) })
    }
  }, [visibleAverages])

  useEffect(() => {
    bidSeriesRef.current?.applyOptions({ visible: showQuotes && bidData.length > 0 })
    askSeriesRef.current?.applyOptions({ visible: showQuotes && askData.length > 0 })
    microSeriesRef.current?.applyOptions({ visible: showQuotes && microData.length > 0 })
  }, [askData.length, bidData.length, microData.length, showQuotes])

  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const microSeries = microSeriesRef.current
    if (!candleSeries || !microSeries) return
    const anchorSeries: CandleApi | LineApi = candleData.length > 0 ? candleSeries : microSeries
    const lines = [
      addPlanLine(anchorSeries, chart.lines.entry, '진입', '#64d9be'),
      addPlanLine(anchorSeries, chart.lines.take_profit, '목표1', '#69bff8'),
      addPlanLine(anchorSeries, chart.lines.take_profit_2, '목표2', '#4be0ea'),
      addPlanLine(anchorSeries, chart.lines.stop, '손절', '#ff7e87'),
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
          text: `모의 진입 ${position.actual_entry}`,
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
          text: `모의 진입 ${trade.entry}`,
        })
      }
      if (exitTime !== undefined) {
        markers.push({
          time: exitTime,
          position: trade.side === 'LONG' ? 'aboveBar' : 'belowBar',
          color: '#f1c96d',
          shape: 'circle',
          text: `종료 ${trade.exit}`,
        })
      }
    }
    markers.sort((left, right) => Number(left.time) - Number(right.time))
    candleMarkers.setMarkers(candleData.length > 0 ? markers : [])
    microMarkers.setMarkers(candleData.length > 0 ? [] : markers)
  }, [candleData, chart.symbol, history, microData, position])

  const plannedLevels = chart.lines.entry !== null
  const toggleAverage = (period: number) => {
    setVisibleAverages((current) =>
      current.includes(period) ? current.filter((item) => item !== period) : [...current, period],
    )
  }

  return (
    <section className="panel chart-panel" aria-labelledby={replay ? 'replay-chart-title' : 'chart-title'}>
      <div className="panel-title chart-title-row">
        <div>
          <p className="section-kicker">{replay ? 'PAST PLAYBACK' : 'PRICE CHART'}</p>
          <h2 id={replay ? 'replay-chart-title' : 'chart-title'}>{chart.symbol} · {chart.interval}</h2>
        </div>
        <span className="fixture-note">{chart.fixture ? '샘플 데이터' : '공개시장 데이터'} · 한국시간</span>
      </div>
      <div className="chart-options" aria-label="차트 표시 선택">
        <span>이동평균선</span>
        {movingAveragePeriods.map((period) => (
          <button
            type="button"
            key={period}
            className={visibleAverages.includes(period) ? 'selected' : ''}
            aria-pressed={visibleAverages.includes(period)}
            style={{ '--line-color': movingAverageColors[period] } as CSSProperties}
            onClick={() => toggleAverage(period)}
          >
            {period}선
          </button>
        ))}
        <button type="button" className={showQuotes ? 'selected' : ''} aria-pressed={showQuotes} onClick={() => setShowQuotes((value) => !value)}>호가선</button>
        <small>선택한 시간구간의 최근 캔들 기준입니다.</small>
      </div>
      {latestCandle ? (
        <dl className="chart-stats" aria-label="최근 캔들 가격">
          <div><dt>현재</dt><dd>{latestCandle.close.toLocaleString()}</dd></div>
          <div><dt>시가</dt><dd>{latestCandle.open.toLocaleString()}</dd></div>
          <div><dt>고가</dt><dd>{latestCandle.high.toLocaleString()}</dd></div>
          <div><dt>저가</dt><dd>{latestCandle.low.toLocaleString()}</dd></div>
          <div><dt>거래량</dt><dd>{latestCandle.volume.toLocaleString()}</dd></div>
        </dl>
      ) : null}
      <div
        ref={containerRef}
        className="chart-wrap"
        role="img"
        aria-label={`${chart.symbol} 실제 캔들·거래량·이동평균선 차트${plannedLevels ? ', 진입 목표 손절선 포함' : ', 확정된 진입 계획 없음'}`}
      >
        {!hasData ? (
          <div className="chart-empty">
            <b>시장 캔들을 기다리고 있습니다.</b>
            <span>실제 공개시장 데이터가 도착하면 자동으로 표시됩니다.</span>
          </div>
        ) : null}
      </div>
      <div className="chart-legend" aria-label="차트 범례">
        <span className="candle-dot">가격 캔들</span>
        <span className="volume-dot">거래량</span>
        {visibleAverages.map((period) => <span className={`ma-${period}-dot`} key={period}>{period}선</span>)}
        <span>한국시간</span>
      </div>
    </section>
  )
}
