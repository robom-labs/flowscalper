// 전략과 Binance 종목 조합의 비용 후 PAPER 통계를 30건 표본 기준과 함께 보여준다.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../api/client'
import { formatPercentFraction, formatRatio, formatUsdt } from '../format'
import { strategyLabel } from '../strategyPresentation'
import type { StrategyRow, StrategySymbolPerformance, StrategySymbolResponse } from '../types'

type Props = { strategies: StrategyRow[] }

export function StrategySymbolPage({ strategies }: Props) {
  const [rows, setRows] = useState<StrategySymbolPerformance[]>([])
  const [excludedPriorVersionSamples, setExcludedPriorVersionSamples] = useState(0)
  const [profile, setProfile] = useState<'BASE' | 'STRESS'>('BASE')
  const [query, setQuery] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void fetchJson<StrategySymbolResponse>('/api/analytics/strategy-symbols', { signal: controller.signal }).then((response) => {
      setRows(response.rows)
      setExcludedPriorVersionSamples(response.excluded_prior_version_samples)
    }).catch(() => {
      setRows([])
      setExcludedPriorVersionSamples(0)
    })
    return () => controller.abort()
  }, [])
  const visible = useMemo(() => rows.filter((row) => row.profile === profile && row.symbol.includes(query.toUpperCase())).sort((left, right) => (left.rank ?? 99_999) - (right.rank ?? 99_999)), [profile, query, rows])
  const observed = rows.filter((row) => row.sample_size > 0).length
  const eligible = rows.filter((row) => row.ranking_eligible).length
  return <section aria-labelledby="strategy-symbol-heading"><div className="page-heading"><div><p className="section-kicker">전략 × 종목</p><h2 id="strategy-symbol-heading">전략별 종목 성과</h2><p className="heading-help">현재 전략 버전의 독립 공개시장 PAPER만 집계합니다. 공동계좌 거래는 중복해서 세지 않습니다.</p></div><span className="page-note">30건 전에는 순위를 매기지 않음 · 과거 버전 {excludedPriorVersionSamples}건 제외</span></div><section className="metric-strip"><article><span>관찰 조합</span><b>{rows.length}개</b></article><article><span>거래 있는 조합</span><b>{observed}개</b></article><article><span>비교 가능한 조합</span><b>{eligible}개</b></article></section><section className="panel strategy-symbol-filters"><label>비용 기준<select value={profile} onChange={(event) => setProfile(event.target.value as 'BASE' | 'STRESS')}><option value="BASE">BASE</option><option value="STRESS">STRESS</option></select></label><label>종목 검색<input value={query} placeholder="BTC" onChange={(event) => setQuery(event.target.value)} /></label></section><section className="panel strategy-performance-panel"><div className="table-scroll"><table className="performance-table"><thead><tr><th>순위</th><th>전략</th><th>종목</th><th>표본</th><th>승률</th><th>기대값</th><th>Profit Factor</th><th>순손익</th><th>비용</th></tr></thead><tbody>{visible.length ? visible.map((row) => <tr key={`${row.strategy_id}-${row.profile}-${row.symbol}`}><td>{row.rank ?? '—'}</td><td>{strategyLabel(strategies.find((strategy) => strategy.strategy_id === row.strategy_id), row.strategy_id)}<small>{row.profile}</small></td><td><strong>{row.symbol}</strong></td><td>{row.sample_size}건<small>{row.ranking_eligible ? '연구 순위 포함' : '데이터 모으는 중'}</small></td><td>{formatPercentFraction(row.win_rate)}</td><td>{formatUsdt(row.expectancy_usdt)}</td><td>{formatRatio(row.profit_factor)}</td><td>{formatUsdt(row.net_pnl, { signed: true })}</td><td>{formatUsdt(Number(row.fees) + Number(row.slippage))}</td></tr>) : <tr><td colSpan={9}>아직 해당 조건의 실제 공개시장 PAPER 표본이 없습니다.</td></tr>}</tbody></table></div></section></section>
}
