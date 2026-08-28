// 전략·종목 조합의 핵심 성과는 쉬운 한국어로, 연구 지표는 접은 정보로 표시한다.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../api/client'
import { costProfileLabel, formatPercentFraction, formatRatio, formatUsdt } from '../format'
import { strategyLabel } from '../strategyPresentation'
import type { StrategyRow, StrategySymbolPerformance, StrategySymbolResponse } from '../types'

type Props = { strategies: StrategyRow[] }

function evidenceLabel(row: StrategySymbolPerformance) {
  if (row.sample_size < 30) return `자료 모으는 중 · ${row.sample_size}/30건`
  return row.ranking_eligible ? '비교 기준 충족' : '추가 검증 필요'
}

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
  const visible = useMemo(
    () => rows
      .filter((row) => row.profile === profile && row.symbol.includes(query.toUpperCase()))
      .sort((left, right) => (left.rank ?? 99_999) - (right.rank ?? 99_999)),
    [profile, query, rows],
  )
  const observed = rows.filter((row) => row.sample_size > 0).length
  const eligible = rows.filter((row) => row.ranking_eligible).length
  return (
    <section aria-labelledby="strategy-symbol-heading">
      <div className="page-heading">
        <div>
          <p className="section-kicker">전략별 종목 결과</p>
          <h2 id="strategy-symbol-heading">어떤 전략이 어떤 종목에 맞았나요?</h2>
          <p className="heading-help">현재 전략 버전의 독립 공개시장 모의거래만 보여줍니다.</p>
        </div>
        <span className="page-note">30건 전에는 순위 없음 · 과거 버전 {excludedPriorVersionSamples}건 보관</span>
      </div>
      <section className="metric-strip">
        <article><span>관찰 조합</span><b>{rows.length}개</b></article>
        <article><span>거래 있는 조합</span><b>{observed}개</b></article>
        <article><span>비교 가능</span><b>{eligible}개</b></article>
      </section>
      <section className="panel strategy-symbol-filters">
        <label>비용 가정<select value={profile} onChange={(event) => setProfile(event.target.value as 'BASE' | 'STRESS')}><option value="BASE">기본 비용</option><option value="STRESS">보수 비용</option></select></label>
        <label>종목 검색<input value={query} placeholder="BTC" onChange={(event) => setQuery(event.target.value)} /></label>
      </section>
      <section className="panel strategy-performance-panel">
        <div className="table-scroll">
          <table className="performance-table">
            <thead><tr><th>비교</th><th>전략·종목</th><th>검증 자료</th><th>승률</th><th>최종 순손익</th><th>자세히</th></tr></thead>
            <tbody>{visible.length ? visible.map((row) => (
              <tr key={`${row.strategy_id}-${row.profile}-${row.symbol}`}>
                <td data-label="비교"><strong>{row.rank ?? '검증 전'}</strong></td>
                <td data-label="전략·종목"><strong>{strategyLabel(strategies.find((strategy) => strategy.strategy_id === row.strategy_id), row.strategy_id)}</strong><small>{row.symbol} · {costProfileLabel(row.profile)}</small></td>
                <td data-label="검증 자료"><strong>{evidenceLabel(row)}</strong></td>
                <td data-label="승률"><strong>{row.sample_size ? formatPercentFraction(row.win_rate) : '표본 없음'}</strong></td>
                <td data-label="최종 순손익" className={Number(row.net_pnl) > 0 ? 'positive' : Number(row.net_pnl) < 0 ? 'negative' : ''}><strong>{formatUsdt(row.net_pnl, { signed: true })}</strong></td>
                <td data-label="자세히"><details className="row-advanced-details"><summary>고급 통계</summary><span>거래당 기대값 {formatUsdt(row.expectancy_usdt)}</span><span>총비용 {formatUsdt(Number(row.fees) + Number(row.slippage))}</span><span>최대 낙폭 {formatUsdt(row.maximum_drawdown)}</span><span>이익합계/손실합계 {formatRatio(row.profit_factor)}</span><span>기술 판단 코드 {row.sample_status}</span></details></td>
              </tr>
            )) : <tr><td colSpan={6}>아직 해당 조건의 공개시장 모의거래 자료가 없습니다.</td></tr>}</tbody>
          </table>
        </div>
      </section>
    </section>
  )
}
