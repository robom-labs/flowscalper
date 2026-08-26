// 불변 원장의 실제 완료 거래를 LIVE·DEMO로 구분하고 체결 상세와 리플레이 동선을 제공한다.
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../api/client'
import { exitReasonLabel, formatDurationMs, formatPrice, formatQuantity, formatUsdt } from '../format'
import type { HistoryResponse, HistoryRow } from '../types'

type Props = {
  rows: HistoryRow[]
  currentRunId: string
  historyScope?: { strategy_version: string; excluded_prior_version_samples: number }
  onReplay: (trade: HistoryRow) => void
}
type Filter = 'ALL' | 'LIVE_PUBLIC' | 'OFFLINE_FIXTURE'
type RunFilter = 'CURRENT' | 'ALL'
type AccountFilter = 'MAIN' | 'LEAGUE' | 'ALL'
type ProfileFilter = 'ALL' | 'BASE' | 'STRESS'
type VersionFilter = 'CURRENT' | 'ALL'

export function HistoryPage({ rows, currentRunId, historyScope, onReplay }: Props) {
  const [filter, setFilter] = useState<Filter>('ALL')
  const [runFilter, setRunFilter] = useState<RunFilter>('CURRENT')
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('ALL')
  const [profileFilter, setProfileFilter] = useState<ProfileFilter>('ALL')
  const [versionFilter, setVersionFilter] = useState<VersionFilter>('CURRENT')
  const [queriedRows, setQueriedRows] = useState<HistoryRow[] | null>(null)
  const [queryLoading, setQueryLoading] = useState(true)
  const [queryError, setQueryError] = useState('')
  const [selectedTrade, setSelected] = useState<HistoryRow | null>(null)
  const needsLedgerQuery = accountFilter !== 'MAIN' || versionFilter !== 'CURRENT'
  const beginQuery = () => {
    setQueriedRows(null)
    setQueryLoading(true)
    setQueryError('')
  }
  useEffect(() => {
    if (!needsLedgerQuery) return
    const controller = new AbortController()
    const query = new URLSearchParams({
      run_scope: runFilter,
      account_scope: accountFilter,
      profile: profileFilter,
      version_scope: versionFilter,
      sample_type: filter,
      limit: '1000',
    })
    const load = () => {
      void fetchJson<HistoryResponse>(`/api/history?${query}`, { signal: controller.signal }, 12_000)
        .then((response) => { setQueriedRows(response.rows); setQueryError('') })
        .catch(() => { if (!controller.signal.aborted) setQueryError('거래 원장 범위를 불러오지 못했습니다. 연결을 확인하세요.') })
        .finally(() => { if (!controller.signal.aborted) setQueryLoading(false) })
    }
    load()
    const timer = window.setInterval(load, 5_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [accountFilter, filter, needsLedgerQuery, profileFilter, runFilter, versionFilter])
  const sourceRows = useMemo(
    () => needsLedgerQuery ? queriedRows ?? [] : rows,
    [needsLedgerQuery, queriedRows, rows],
  )
  const filtered = useMemo(
    () => sourceRows.filter((row) => (
      (runFilter === 'ALL' || row.run_id === currentRunId)
      && (filter === 'ALL' || row.sample_type === filter)
      && (profileFilter === 'ALL' || row.profile === profileFilter)
    )),
    [currentRunId, filter, profileFilter, runFilter, sourceRows],
  )
  const selected = selectedTrade
    && filtered.some((row) => row.trade_id === selectedTrade.trade_id)
    ? selectedTrade
    : null
  const mainCount = filtered.filter((row) => row.account_scope !== 'LEAGUE').length
  const leagueCount = filtered.length - mainCount
  const visibleQueryLoading = needsLedgerQuery && queryLoading
  return (
    <section aria-labelledby="history-heading">
      <div className="page-heading">
        <div><p className="section-kicker">TRADE HISTORY</p><h2 id="history-heading">거래 기록</h2><p className="heading-help">공동계좌와 전략별 가상계좌의 종료된 모의거래를 함께 표시합니다.{historyScope?.excluded_prior_version_samples ? ` 과거 버전 ${historyScope.excluded_prior_version_samples}건은 원장에 보관되어 있습니다.` : ''}</p></div>
        <div className="history-heading-filters"><label className="inline-filter">Run 범위<select value={runFilter} onChange={(event) => { beginQuery(); setRunFilter(event.target.value as RunFilter) }}><option value="CURRENT">이번 Run</option><option value="ALL">전체 Run</option></select></label><label className="inline-filter">계좌 범위<select aria-label="계좌 범위" value={accountFilter} onChange={(event) => { beginQuery(); setAccountFilter(event.target.value as AccountFilter) }}><option value="MAIN">공동 PAPER</option><option value="LEAGUE">전략별 PAPER</option><option value="ALL">모든 PAPER 계좌</option></select></label><label className="inline-filter">비용 조건<select aria-label="비용 조건" value={profileFilter} onChange={(event) => { beginQuery(); setProfileFilter(event.target.value as ProfileFilter) }}><option value="ALL">BASE + STRESS</option><option value="BASE">기본 비용</option><option value="STRESS">강화 비용</option></select></label><label className="inline-filter">전략 버전<select aria-label="전략 버전" value={versionFilter} onChange={(event) => { beginQuery(); setVersionFilter(event.target.value as VersionFilter) }}><option value="CURRENT">현재 버전</option><option value="ALL">과거 버전 포함</option></select></label><label className="inline-filter">기록 구분<select value={filter} onChange={(event) => { beginQuery(); setFilter(event.target.value as Filter) }}><option value="ALL">전체</option><option value="LIVE_PUBLIC">공개시장 모의거래</option><option value="OFFLINE_FIXTURE">샘플 거래</option></select></label></div>
      </div>
      {queryError ? <p className="error-banner" role="alert">{queryError}</p> : null}
      <p className="history-result-summary" role="status">{visibleQueryLoading ? '거래 기록을 불러오는 중입니다.' : `표시 ${filtered.length}건 · 공동계좌 ${mainCount}건 · 전략별 계좌 ${leagueCount}건`}</p>
      <div className={selected ? 'history-layout drawer-open' : 'history-layout'}>
        <section className="panel wide-panel table-scroll">
          <table className="history-table"><thead><tr><th>Run / 거래</th><th>종목</th><th>전략 / 계좌</th><th>체결→종료</th><th>종료사유</th><th>총손익</th><th>비용</th><th>순손익</th><th>보유</th><th>보기</th></tr></thead>
            <tbody>{filtered.map((row) => <tr key={`${row.account_scope ?? 'MAIN'}:${row.trade_id}`}><td><strong>{row.trade_id}</strong><small>{row.run_id}<br />{row.sample_type === 'LIVE_PUBLIC' ? '공개시장 PAPER' : '오프라인 DEMO'}</small></td><td>{row.symbol}<small>{row.side}</small></td><td>{row.strategy}<small>{row.profile} · {row.account_scope === 'LEAGUE' ? '전략별 계좌' : '공동계좌'}{row.strategy_version ? ` · ${row.strategy_version === historyScope?.strategy_version ? '현재 전략 버전' : '과거 전략 버전'}` : ''}</small></td><td>{formatPrice(row.entry)} → {formatPrice(row.exit)}</td><td>{exitReasonLabel(row.exit_reason)}<small>{row.exit_reason}</small></td><td>{formatUsdt(row.gross_pnl)}</td><td>수수료 {formatUsdt(row.fees)}<br />슬리피지 {formatUsdt(row.slippage)}</td><td className={Number(row.net_pnl) >= 0 ? 'positive' : 'negative'}>{formatUsdt(row.net_pnl, { signed: true })}</td><td>{formatDurationMs(row.holding_ms)}</td><td><div className="table-actions"><button type="button" className="table-button" onClick={() => setSelected(row)}>상세</button><button type="button" className="table-button" disabled={row.replay_available === false} title={row.replay_available === false ? '이 Run에는 저장된 공개시장 이벤트가 없습니다.' : undefined} onClick={() => onReplay(row)}>{row.replay_available === false ? '재생 자료 없음' : '재생'}</button></div></td></tr>)}</tbody>
          </table>
          {!visibleQueryLoading && !queryError && filtered.length === 0 ? <p className="empty-copy">선택한 범위에는 완료된 PAPER 거래가 없습니다. Run·계좌·비용·버전 범위를 바꿔 확인하세요.</p> : null}
        </section>
        {selected ? <aside className="panel trade-drawer" aria-labelledby="trade-detail-title"><div className="panel-title"><h3 id="trade-detail-title">거래 상세</h3><button type="button" className="close-button" aria-label="거래 상세 닫기" onClick={() => setSelected(null)}>닫기</button></div><dl className="detail-list"><div><dt>거래 ID</dt><dd>{selected.trade_id}</dd></div><div><dt>종목 / 방향</dt><dd>{selected.symbol} · {selected.side}</dd></div><div><dt>전략</dt><dd>{selected.strategy}</dd></div><div><dt>수량</dt><dd>{formatQuantity(selected.quantity)}</dd></div><div><dt>실제 진입</dt><dd>{formatPrice(selected.entry)}</dd></div><div><dt>초기 손절</dt><dd>{formatPrice(selected.initial_stop)}</dd></div><div><dt>목표가</dt><dd>{formatPrice(selected.take_profit)}</dd></div><div><dt>실제 종료</dt><dd>{formatPrice(selected.exit)}</dd></div><div><dt>종료 사유</dt><dd>{exitReasonLabel(selected.exit_reason)}</dd></div><div><dt>보유시간</dt><dd>{formatDurationMs(selected.holding_ms)}</dd></div><div><dt>수수료 / 슬리피지</dt><dd>{formatUsdt(selected.fees)} / {formatUsdt(selected.slippage)}</dd></div><div><dt>순손익</dt><dd>{formatUsdt(selected.net_pnl, { signed: true })}</dd></div></dl><button type="button" className="primary-button full-width" onClick={() => onReplay(selected)}>이 Run 리플레이 열기</button></aside> : null}
      </div>
    </section>
  )
}
