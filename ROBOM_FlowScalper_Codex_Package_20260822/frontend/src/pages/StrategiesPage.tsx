// 여섯 전략의 핵심 상태는 짧은 표로, BASE·STRESS 세부 성과는 drawer로 분리한다.
import { useCallback, useMemo, useState } from 'react'
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
      <div className="page-heading"><div><p className="section-kicker">PAPER 전략</p><h2 id="strategies-heading">전략 설정</h2><p className="heading-help">전략별 켜기·관찰만·끄기와 상승·하락 방향을 정합니다.</p></div><span className="page-note">각 BASE와 STRESS 가상계좌는 서로 독립적입니다.</span></div>
      {ordered.length === 0 ? <div className="panel empty-state"><b>전략 정보를 불러오는 중입니다.</b></div> : null}
      <section className="panel strategy-compact-panel"><div className="table-scroll"><table className="strategy-compact-table"><thead><tr><th>전략</th><th>사용 상태</th><th>방향</th><th>현재 PAPER</th><th>완료</th><th>승률</th><th>표본</th><th>상세</th></tr></thead><tbody>{ordered.map((strategy) => {
        const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === 'BASE')
        const pnl = account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0
        const winRate = account?.win_rate === null || account?.win_rate === undefined ? '표본 없음' : `${(Number(account.win_rate) * 100).toFixed(1)}%`
        const isSaving = saving === strategy.strategy_id
        return <tr key={strategy.strategy_id} data-strategy-id={strategy.strategy_id}><td><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko} · {strategy.stability === 'STABLE' ? '안정' : '시험 중'}</small></td><td><div className="strategy-inline-modes">{(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => <button type="button" aria-label={`${strategy.short_name} ${modeLabels[mode]}`} aria-pressed={strategy.mode === mode} disabled={isSaving} key={mode} onClick={() => void configure(strategy, { mode })}>{strategy.mode === mode && isSaving ? '저장 중' : modeLabels[mode]}</button>)}</div></td><td><div className="strategy-inline-directions"><button type="button" aria-pressed={strategy.long_enabled} disabled={isSaving} onClick={() => void configure(strategy, { long_enabled: !strategy.long_enabled })}>상승 {strategy.long_enabled ? '켜짐' : '꺼짐'}</button><button type="button" aria-pressed={strategy.short_enabled} disabled={isSaving} onClick={() => void configure(strategy, { short_enabled: !strategy.short_enabled })}>하락 {strategy.short_enabled ? '켜짐' : '꺼짐'}</button></div></td><td><span className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>{signed(pnl)}</span><small>자산 {account?.current_equity_usdt ?? '1000'}</small></td><td>{account?.trade_count ?? 0}건<small>진행 {account?.open_positions ?? 0}건</small></td><td>{winRate}</td><td>{strategy.performance.BASE.sample_status}</td><td><button type="button" className="secondary-button" onClick={() => setSelected(strategy)}>자세히</button></td></tr>
      })}</tbody></table></div></section>
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
