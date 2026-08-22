// 실제 완료 PAPER 거래의 순손익으로 자산곡선과 낙폭을 계산해 차트로 표시한다.
import { AreaSeries, ColorType, LineSeries, createChart, type UTCTimestamp } from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'
import type { HistoryRow } from '../types'

export function PerformanceCurve({ history }: { history: HistoryRow[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const series = useMemo(() => {
    const ordered = [...history]
      .sort((left, right) => left.exit_ts_ms - right.exit_ts_ms)
    return ordered.reduce<{
      equity: number
      peak: number
      previousTime: number
      points: { time: UTCTimestamp; equity: number; drawdown: number }[]
    }>((state, trade) => {
        const equity = state.equity + Number(trade.net_pnl)
        const peak = Math.max(state.peak, equity)
        const original = Math.floor(trade.exit_ts_ms / 1_000)
        const timestamp = Math.max(original, state.previousTime + 1)
        return {
          equity,
          peak,
          previousTime: timestamp,
          points: [...state.points, {
            time: timestamp as UTCTimestamp,
            equity,
            drawdown: Math.max(0, peak - equity),
          }],
        }
      }, { equity: 1000, peak: 1000, previousTime: 0, points: [] }).points
  }, [history])

  useEffect(() => {
    const container = containerRef.current
    if (!container || series.length === 0) return
    const chart = createChart(container, {
      width: container.clientWidth,
      height: Math.max(260, container.clientHeight),
      layout: {
        background: { type: ColorType.Solid, color: '#071219' },
        textColor: '#7893a2',
        attributionLogo: false,
      },
      grid: { vertLines: { color: '#142a35' }, horzLines: { color: '#142a35' } },
      rightPriceScale: { borderColor: '#1b3441' },
      timeScale: { borderColor: '#1b3441', timeVisible: true },
    })
    const equitySeries = chart.addSeries(LineSeries, {
      color: '#64d9be', lineWidth: 2, priceLineVisible: false, title: '자산',
    })
    const drawdownSeries = chart.addSeries(AreaSeries, {
      lineColor: '#ff7e87', topColor: '#ff7e8740', bottomColor: '#ff7e8704',
      lineWidth: 1, priceLineVisible: false, title: '낙폭', priceScaleId: 'drawdown',
    })
    equitySeries.setData(series.map((point) => ({ time: point.time, value: point.equity })))
    drawdownSeries.setData(series.map((point) => ({ time: point.time, value: point.drawdown })))
    chart.priceScale('drawdown').applyOptions({ scaleMargins: { top: 0.72, bottom: 0 } })
    chart.timeScale().fitContent()
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (entry) chart.applyOptions({ width: Math.floor(entry.contentRect.width) })
    })
    resizeObserver.observe(container)
    return () => { resizeObserver.disconnect(); chart.remove() }
  }, [series])

  return <div className="performance-chart" ref={containerRef} role="img" aria-label="완료 PAPER 거래 기반 자산곡선과 낙폭 차트">{series.length === 0 ? <div className="chart-empty"><b>완료 거래 표본이 없습니다</b><span>거래가 종료되면 실제 자산곡선과 낙폭을 계산합니다.</span></div> : null}</div>
}
