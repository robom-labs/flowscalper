// 여섯 전략의 독립 BASE·STRESS PAPER 계좌를 카드와 고정 drawer로 표시한다.
import { memo, useCallback, useMemo, useState } from 'react'
import { SideDrawer } from '../components/SideDrawer'
import { modeLabels, orderedStrategies } from '../strategyPresentation'
import type { LeagueAccount, StrategyPerformance, StrategyRow } from '../types'

type StrategyConfiguration = {
  mode: 'ACTIVE' | 'SHADOW' | 'OFF'
  long_enabled: boolean
  short_enabled: boolean
}

type Props = {
  strategies: StrategyRow[]
  leagueAccounts: LeagueAccount[]
  onConfigure: (strategyId: string, configuration: StrategyConfiguration) => Promise<unknown>
}

function number(value: string) {
  return Number(value || 0)
}

function signed(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(4)} USDT`
}

function printable(value: string | number | null, suffix = '') {
  return value === null ? '표본 없음' : `${value}${suffix}`
}

const StrategyCard = memo(function StrategyCard({
  strategy,
  account,
  saving,
  onConfigure,
  onDetail,
}: {
  strategy: StrategyRow
  account: LeagueAccount | undefined
  saving: boolean
  onConfigure: (strategy: StrategyRow, configuration: Partial<StrategyConfiguration>) => void
  onDetail: (strategy: StrategyRow) => void
}) {
  const pnl = account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0
  const trades = account?.trade_count ?? 0
  const winRate = account?.win_rate === null || account?.win_rate === undefined
    ? '표본 없음'
    : `${(Number(account.win_rate) * 100).toFixed(1)}%`
  return (
    <article className="panel strategy-card" data-strategy-id={strategy.strategy_id}>
      <div className="strategy-card-heading">
        <div>
          <span className={strategy.stability === 'STABLE' ? 'strategy-stability stable' : 'strategy-stability experimental'}>{strategy.stability === 'STABLE' ? '안정 전략' : '시험 중'}</span>
          <h3>{strategy.short_name} · {strategy.display_name_ko}</h3>
          <p>{strategy.summary_ko}</p>
        </div>
        <span className="strategy-state">{saving ? '저장 중' : modeLabels[strategy.mode]}</span>
      </div>
      <div className="strategy-primary-metrics">
        <div><span>BASE 현재자산</span><b>{account?.current_equity_usdt ?? '1000'} USDT</b></div>
        <div><span>BASE 순손익</span><b className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>{signed(pnl)}</b></div>
        <div><span>완료 거래</span><b>{trades}건</b></div>
        <div><span>승리 · 손실</span><b>{account?.wins ?? 0} · {account?.losses ?? 0}</b></div>
        <div><span>승률</span><b>{winRate}</b></div>
        <div><span>열린 거래</span><b>{account?.open_positions ?? 0}건</b></div>
        <div><span>최대 낙폭</span><b>{account?.maximum_drawdown_usdt ?? '0'} USDT</b></div>
        <div><span>표본 상태</span><b>{strategy.performance.BASE.sample_status}</b></div>
      </div>
      <fieldset className="mode-control" disabled={saving}>
        <legend>사용 범위</legend>
        {(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => (
          <button type="button" className={strategy.mode === mode ? 'selected' : ''} aria-pressed={strategy.mode === mode} key={mode} onClick={() => onConfigure(strategy, { mode })}>{modeLabels[mode]}</button>
        ))}
      </fieldset>
      <div className="direction-controls">
        <button type="button" className={strategy.long_enabled ? 'direction-on' : 'direction-off'} aria-pressed={strategy.long_enabled} disabled={saving} onClick={() => onConfigure(strategy, { long_enabled: !strategy.long_enabled })}>상승 방향 {strategy.long_enabled ? '사용' : '사용 안 함'}</button>
        <button type="button" className={strategy.short_enabled ? 'direction-on' : 'direction-off'} aria-pressed={strategy.short_enabled} disabled={saving} onClick={() => onConfigure(strategy, { short_enabled: !strategy.short_enabled })}>하락 방향 {strategy.short_enabled ? '사용' : '사용 안 함'}</button>
      </div>
      <div className="strategy-card-footer"><span>현재 상태 · {strategy.latest_status === 'WAITING_DATA' ? '시장 데이터 기다리는 중' : strategy.latest_status}</span><button type="button" className="secondary-button" onClick={() => onDetail(strategy)}>자세히 보기</button></div>
    </article>
  )
})

function ProfileDetails({ report, account }: { report: StrategyPerformance; account: LeagueAccount | undefined }) {
  const windows = ['recent_50', 'recent_100', 'recent_300'] as const
  return (
    <section className="profile-detail-block">
      <h3>{report.profile} 가상계좌</h3>
      <dl className="drawer-detail-list">
        <div><dt>현재자산</dt><dd>{account?.current_equity_usdt ?? '1000'} USDT</dd></div>
        <div><dt>순손익</dt><dd>{account ? signed(number(account.current_equity_usdt) - number(account.starting_equity_usdt)) : '+0.0000 USDT'}</dd></div>
        <div><dt>거래 수</dt><dd>{report.sample_size}건</dd></div>
        <div><dt>승률</dt><dd>{report.win_rate === null ? '표본 없음' : `${(Number(report.win_rate) * 100).toFixed(1)}%`}</dd></div>
        <div><dt>평균 이익</dt><dd>{printable(report.average_win_usdt, ' USDT')}</dd></div>
        <div><dt>평균 손실</dt><dd>{printable(report.average_loss_usdt, ' USDT')}</dd></div>
        <div><dt>손익비</dt><dd>{printable(report.payoff_ratio)}</dd></div>
        <div><dt>거래당 기대값</dt><dd>{printable(report.expectancy_usdt, ' USDT')}</dd></div>
        <div><dt>기대값 R · bp</dt><dd>{printable(report.expectancy_r, ' R')} · {printable(report.expectancy_bps, ' bp')}</dd></div>
        <div><dt>Profit Factor</dt><dd>{printable(report.profit_factor)}</dd></div>
        <div><dt>수수료 · 슬리피지</dt><dd>{report.fees} · {report.slippage}</dd></div>
        <div><dt>비용 부담</dt><dd>{printable(report.cost_burden)}</dd></div>
        <div><dt>최대 낙폭</dt><dd>{report.maximum_drawdown} USDT</dd></div>
        <div><dt>평균 MAE · MFE</dt><dd>{printable(report.mae_r_mean, ' R')} · {printable(report.mfe_r_mean, ' R')}</dd></div>
        <div><dt>보유 중앙 · p90</dt><dd>{report.median_hold_ms === null ? '표본 없음' : `${(report.median_hold_ms / 1000).toFixed(1)}초`} · {report.p90_hold_ms === null ? '표본 없음' : `${(report.p90_hold_ms / 1000).toFixed(1)}초`}</dd></div>
        <div><dt>LONG · SHORT</dt><dd>{report.sides.LONG} · {report.sides.SHORT}</dd></div>
        <div><dt>종목 · 시장상태</dt><dd>{report.symbols.length}개 · {report.regime_count}개</dd></div>
        <div><dt>표본상태</dt><dd>{report.sample_status}</dd></div>
        <div><dt>판단</dt><dd>{report.recommendation} · 참고용</dd></div>
      </dl>
      <div className="window-summary">{windows.map((key) => {
        const value = report.windows[key]
        const size = typeof value?.sample_size === 'number' ? value.sample_size : 0
        return <span key={key}>{key.replace('recent_', '최근 ')} · {size}건</span>
      })}</div>
    </section>
  )
}

export function StrategiesPage({ strategies, leagueAccounts, onConfigure }: Props) {
  const [saving, setSaving] = useState('')
  const [selected, setSelected] = useState<StrategyRow | null>(null)
  const ordered = useMemo(() => orderedStrategies(strategies), [strategies])
  const configure = useCallback(async (strategy: StrategyRow, configuration: Partial<StrategyConfiguration>) => {
    setSaving(strategy.strategy_id)
    try {
      await onConfigure(strategy.strategy_id, {
        mode: strategy.mode,
        long_enabled: strategy.long_enabled,
        short_enabled: strategy.short_enabled,
        ...configuration,
      })
    } finally {
      setSaving('')
    }
  }, [onConfigure])
  const closeDrawer = useCallback(() => setSelected(null), [])
  const accounts = selected ? leagueAccounts.filter((account) => account.strategy_id === selected.strategy_id) : []
  return (
    <section aria-labelledby="strategies-heading">
      <div className="page-heading"><div><p className="section-kicker">STRATEGY LEAGUE</p><h2 id="strategies-heading">전략 리그</h2><p className="heading-help">각 전략은 BASE와 STRESS 가상계좌를 각각 1,000 USDT로 독립 운영합니다.</p></div><span className="page-note">독립 계좌 결과는 실제 공동자금 합계가 아닙니다.</span></div>
      {ordered.length === 0 ? <div className="panel empty-state"><b>전략 리그를 불러오는 중입니다.</b></div> : null}
      <div className="strategy-grid">
        {ordered.map((strategy) => <StrategyCard key={strategy.strategy_id} strategy={strategy} account={leagueAccounts.find((account) => account.strategy_id === strategy.strategy_id && account.profile === 'BASE')} saving={saving === strategy.strategy_id} onConfigure={(item, configuration) => void configure(item, configuration)} onDetail={setSelected} />)}
      </div>
      <SideDrawer title={selected ? `${selected.short_name} · ${selected.display_name_ko}` : '전략 상세'} open={selected !== null} onClose={closeDrawer} label="전략 상세 정보">
        {selected ? <>
          <p className="drawer-subtitle"><b>{modeLabels[selected.mode]}</b> · strategy_id {selected.strategy_id}</p>
          <ProfileDetails report={selected.performance.BASE} account={accounts.find((account) => account.profile === 'BASE')} />
          <ProfileDetails report={selected.performance.STRESS} account={accounts.find((account) => account.profile === 'STRESS')} />
        </> : null}
      </SideDrawer>
    </section>
  )
}
