// 여섯 전략의 핵심 상태는 짧은 표로, BASE·STRESS 세부 성과는 drawer로 분리한다.
import { useCallback, useMemo, useState } from 'react'
import { SideDrawer } from '../components/SideDrawer'
import { formatDurationMs, formatPercentFraction, formatRatio, formatUsdt } from '../format'
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

function ProfileDetails({ report, account }: { report: StrategyPerformance; account: LeagueAccount | undefined }) {
  const windows = ['recent_50', 'recent_100', 'recent_300'] as const
  return (
    <section className="profile-detail-block">
      <h3>{report.profile} 가상계좌</h3>
      <p className="profile-scope-note">자산·순손익은 이번 Run, 아래 통계는 저장된 전체 독립 PAPER 거래 기준입니다.</p>
      <dl className="drawer-detail-list">
        <div><dt>이번 Run 현재자산</dt><dd>{formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</dd></div>
        <div><dt>이번 Run 순손익</dt><dd>{formatUsdt(account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0, { signed: true })}</dd></div>
        <div><dt>저장된 완료 표본</dt><dd>{report.sample_size}건</dd></div>
        <div><dt>승 · 패 · 보합</dt><dd>{report.wins} · {report.losses} · {report.breakevens}</dd></div>
        <div><dt>승률</dt><dd>{report.win_rate === null ? '표본 없음' : formatPercentFraction(report.win_rate)}<small>{report.win_rate_ci95 ? ` · 95% 범위 ${formatPercentFraction(report.win_rate_ci95.lower)}~${formatPercentFraction(report.win_rate_ci95.upper)}` : ''}</small></dd></div>
        <div><dt>평균 이익</dt><dd>{report.average_win_usdt === null ? '표본 없음' : formatUsdt(report.average_win_usdt)}</dd></div>
        <div><dt>평균 손실</dt><dd>{report.average_loss_usdt === null ? '표본 없음' : formatUsdt(report.average_loss_usdt)}</dd></div>
        <div><dt>손익비</dt><dd>{formatRatio(report.payoff_ratio)}</dd></div>
        <div><dt>거래당 기대값</dt><dd>{formatUsdt(report.expectancy_usdt)}</dd></div>
        <div><dt>기대값 R · bp</dt><dd>{formatRatio(report.expectancy_r, ' R')} · {formatRatio(report.expectancy_bps, ' bp')}</dd></div>
        <div><dt>Profit Factor</dt><dd>{formatRatio(report.profit_factor)}</dd></div>
        <div><dt>수수료 · 슬리피지</dt><dd>{formatUsdt(report.fees)} · {formatUsdt(report.slippage)}</dd></div>
        <div><dt>비용 부담</dt><dd>{formatPercentFraction(report.cost_burden)}</dd></div>
        <div><dt>최대 낙폭</dt><dd>{formatUsdt(report.maximum_drawdown)}</dd></div>
        <div><dt>평균 MAE · MFE</dt><dd>{formatRatio(report.mae_r_mean, ' R')} · {formatRatio(report.mfe_r_mean, ' R')}</dd></div>
        <div><dt>보유 중앙 · p90</dt><dd>{formatDurationMs(report.median_hold_ms)} · {formatDurationMs(report.p90_hold_ms)}</dd></div>
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
  const enabledCount = ordered.filter((strategy) => strategy.mode !== 'OFF' && (strategy.long_enabled || strategy.short_enabled)).length
  return (
    <section aria-labelledby="strategies-heading">
      <div className="page-heading"><div><p className="section-kicker">PAPER 전략</p><h2 id="strategies-heading">전략 설정</h2><p className="heading-help">기본값은 6개 전략 모두 켜짐입니다. A·B는 공동계좌와 독립계좌, C~F는 독립계좌에서만 모의합니다.</p></div><span className="page-note">{enabledCount}/{ordered.length || 6} 전략 켜짐 · 실제 주문 0</span></div>
      {ordered.length === 0 ? <div className="panel empty-state"><b>전략 정보를 불러오는 중입니다.</b></div> : null}
      <section className="panel strategy-compact-panel"><div className="table-scroll"><table className="strategy-compact-table"><thead><tr><th>전략</th><th>사용 상태</th><th>방향</th><th>현재 PAPER</th><th>완료</th><th>승률</th><th>표본</th><th>상세</th></tr></thead><tbody>{ordered.map((strategy) => {
        const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === 'BASE')
        const pnl = account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0
        const winRate = account?.win_rate === null || account?.win_rate === undefined ? '표본 없음' : formatPercentFraction(account.win_rate)
        const isSaving = saving === strategy.strategy_id
        return <tr key={strategy.strategy_id} data-strategy-id={strategy.strategy_id}><td><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko} · {strategy.stability === 'STABLE' ? '안정' : '시험 중'}</small></td><td><div className="strategy-inline-modes">{(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => <button type="button" aria-label={`${strategy.short_name} ${modeLabels[mode]}`} aria-pressed={strategy.mode === mode} disabled={isSaving} key={mode} onClick={() => void configure(strategy, { mode })}>{strategy.mode === mode && isSaving ? '저장 중' : modeLabels[mode]}</button>)}</div></td><td><div className="strategy-inline-directions"><button type="button" aria-pressed={strategy.long_enabled} disabled={isSaving} onClick={() => void configure(strategy, { long_enabled: !strategy.long_enabled })}>상승 {strategy.long_enabled ? '켜짐' : '꺼짐'}</button><button type="button" aria-pressed={strategy.short_enabled} disabled={isSaving} onClick={() => void configure(strategy, { short_enabled: !strategy.short_enabled })}>하락 {strategy.short_enabled ? '켜짐' : '꺼짐'}</button></div></td><td><span className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>{formatUsdt(pnl, { signed: true })}</span><small>자산 {formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</small></td><td>{account?.trade_count ?? 0}건<small>진행 {account?.open_positions ?? 0}건</small></td><td>{winRate}</td><td>{strategy.performance.BASE.sample_status}</td><td><button type="button" className="secondary-button" onClick={() => setSelected(strategy)}>자세히</button></td></tr>
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
