// 실제 캔들·호가·불변 거래계획과 PAPER 체결 마커를 Lightweight Charts로 표시한다.
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'
import type { ChartData, CurrentPosition, HistoryRow } from '../types'

type Props = {
  chart: ChartData
  position?: CurrentPosition | null
  history?: HistoryRow[]
  replay?: boolean
}

type QuotePoint = { time: UTCTimestamp; value: number }

function quoteSeries(chart: ChartData, key: 'bid' | 'ask' | 'mid' | 'microprice') {
  const bySecond = new Map<number, QuotePoint>()
  for (const point of chart.points) {
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
  series: ISeriesApi<'Candlestick' | 'Line', Time>,
  value: number | null | undefined,
  title: string,
  color: string,
) {
  if (value === null || value === undefined || !Number.isFinite(value)) return
  series.createPriceLine({
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

  useEffect(() => {
    const container = containerRef.current
    if (!container || (candleData.length === 0 && chart.points.length === 0)) return

    const lightweightChart = createChart(container, {
      width: container.clientWidth,
      height: Math.max(310, container.clientHeight),
      layout: {
        background: { type: ColorType.Solid, color: '#071219' },
        textColor: '#7893a2',
        fontFamily: 'Inter, Pretendard, ui-sans-serif, system-ui, sans-serif',
        attributionLogo: false,
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
        secondsVisible: chart.interval.endsWith('s'),
        rightOffset: 3,
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
    candleSeries.setData(candleData)

    const bidData = quoteSeries(chart, 'bid')
    const askData = quoteSeries(chart, 'ask')
    const microData = quoteSeries(chart, 'microprice')
    const bidSeries = lightweightChart.addSeries(LineSeries, {
      color: '#5998c6',
      lineWidth: 1,
      lineVisible: bidData.length > 0,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const askSeries = lightweightChart.addSeries(LineSeries, {
      color: '#c5767d',
      lineWidth: 1,
      lineVisible: askData.length > 0,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const microSeries = lightweightChart.addSeries(LineSeries, {
      color: '#d9e8ee',
      lineWidth: 2,
      lineVisible: microData.length > 0,
      priceLineVisible: false,
      lastValueVisible: true,
    })
    bidSeries.setData(bidData)
    askSeries.setData(askData)
    microSeries.setData(microData)

    const anchorSeries: ISeriesApi<'Candlestick' | 'Line', Time> =
      candleData.length > 0 ? candleSeries : microSeries
    addPlanLine(anchorSeries, chart.lines.entry, '진입', '#64d9be')
    addPlanLine(anchorSeries, chart.lines.take_profit, 'TP1', '#69bff8')
    addPlanLine(anchorSeries, chart.lines.take_profit_2, 'TP2', '#4be0ea')
    addPlanLine(anchorSeries, chart.lines.stop, 'SL', '#ff7e87')

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
    if (markers.length > 0) {
      createSeriesMarkers(
        anchorSeries,
        markers.sort((left, right) => Number(left.time) - Number(right.time)),
      )
    }

    lightweightChart.timeScale().fitContent()
    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        lightweightChart.applyOptions({
          width: Math.floor(entry.contentRect.width),
          height: Math.max(310, Math.floor(entry.contentRect.height)),
        })
      }
    })
    resizeObserver.observe(container)
    return () => {
      resizeObserver.disconnect()
      lightweightChart.remove()
    }
  }, [candleData, chart, history, position])

  const hasData = candleData.length > 0 || chart.points.length > 0
  const plannedLevels = chart.lines.entry !== null

  return (
    <section className="panel chart-panel" aria-labelledby={replay ? 'replay-chart-title' : 'chart-title'}>
      <div className="panel-title">
        <div>
          <p className="section-kicker">{replay ? 'DETERMINISTIC REPLAY' : 'EXECUTABLE MICROSTRUCTURE'}</p>
          <h2 id={replay ? 'replay-chart-title' : 'chart-title'}>{chart.symbol} · {chart.interval}</h2>
        </div>
        <span className="fixture-note">{chart.fixture ? 'OFFLINE DEMO' : 'LIVE PUBLIC'}</span>
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
      </div>
    </section>
  )
}
