// bid·ask·mid·microprice와 진입·TP·SL을 원본 SVG 차트로 표시한다.
import { useMemo } from 'react'
import type { ChartData } from '../types'

type Props = { chart: ChartData; replay?: boolean }

const WIDTH = 760
const HEIGHT = 300
const PAD = 34

export function PriceChart({ chart, replay = false }: Props) {
  const geometry = useMemo(() => {
    const values = chart.points.flatMap((point) => [point.bid, point.ask, point.mid, point.microprice])
    values.push(chart.lines.entry, chart.lines.take_profit, chart.lines.stop)
    const minimum = Math.min(...values)
    const maximum = Math.max(...values)
    const span = Math.max(0.0001, maximum - minimum)
    const x = (index: number) => PAD + (index / Math.max(1, chart.points.length - 1)) * (WIDTH - PAD * 2)
    const y = (value: number) => HEIGHT - PAD - ((value - minimum) / span) * (HEIGHT - PAD * 2)
    const line = (selector: (point: ChartData['points'][number]) => number) =>
      chart.points.map((point, index) => `${x(index)},${y(selector(point))}`).join(' ')
    return {
      bid: line((point) => point.bid),
      ask: line((point) => point.ask),
      mid: line((point) => point.mid),
      microprice: line((point) => point.microprice),
      y,
    }
  }, [chart])

  return (
    <section className="panel chart-panel" aria-labelledby={replay ? 'replay-chart-title' : 'chart-title'}>
      <div className="panel-title">
        <div><p className="section-kicker">{replay ? 'DETERMINISTIC REPLAY' : 'EXECUTABLE MICROSTRUCTURE'}</p><h2 id={replay ? 'replay-chart-title' : 'chart-title'}>{chart.symbol} · {chart.interval}</h2></div>
        <span className="fixture-note">{chart.fixture ? 'OFFLINE FIXTURE' : 'LIVE PUBLIC'}</span>
      </div>
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${chart.symbol} 차트, 진입 TP SL 선 포함`}>
          <defs><pattern id="grid" width="76" height="50" patternUnits="userSpaceOnUse"><path d="M 76 0 L 0 0 0 50" fill="none" stroke="#19303c" strokeWidth="1" /></pattern></defs>
          <rect width={WIDTH} height={HEIGHT} fill="url(#grid)" />
          <polyline points={geometry.bid} className="chart-bid" />
          <polyline points={geometry.ask} className="chart-ask" />
          <polyline points={geometry.mid} className="chart-mid" />
          <polyline points={geometry.microprice} className="chart-micro" />
          <line x1={PAD} x2={WIDTH - PAD} y1={geometry.y(chart.lines.take_profit)} y2={geometry.y(chart.lines.take_profit)} className="level tp-level" />
          <line x1={PAD} x2={WIDTH - PAD} y1={geometry.y(chart.lines.entry)} y2={geometry.y(chart.lines.entry)} className="level entry-level" />
          <line x1={PAD} x2={WIDTH - PAD} y1={geometry.y(chart.lines.stop)} y2={geometry.y(chart.lines.stop)} className="level sl-level" />
          <text x={WIDTH - PAD} y={geometry.y(chart.lines.take_profit) - 5} className="label tp-text">TP {chart.lines.take_profit.toFixed(2)}</text>
          <text x={WIDTH - PAD} y={geometry.y(chart.lines.entry) - 5} className="label entry-text">진입 {chart.lines.entry.toFixed(2)}</text>
          <text x={WIDTH - PAD} y={geometry.y(chart.lines.stop) - 5} className="label sl-text">SL {chart.lines.stop.toFixed(2)}</text>
        </svg>
      </div>
      <div className="chart-legend"><span className="bid-dot">bid</span><span className="ask-dot">ask</span><span className="mid-dot">mid</span><span className="micro-dot">microprice</span></div>
    </section>
  )
}

