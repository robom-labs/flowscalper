// 전략의 핵심 상태는 짧은 표로, BASE·STRESS 세부 성과는 drawer로 분리한다.
import { useCallback, useMemo, useState } from 'react'
import { SideDrawer } from '../components/SideDrawer'
import { formatDurationMs, formatPercentFraction, formatRatio, formatUsdt } from '../format'
import { modeLabels, orderedStrategies, strategyLabel, strategyWaitReasonLabel } from '../strategyPresentation'
import type { LeagueAccount, StrategyPerformance, StrategyRow } from '../types'

type StrategyConfiguration = {
  mode: 'ACTIVE' | 'SHADOW' | 'OFF'
  long_enabled: boolean
  short_enabled: boolean
  expected_revision: number
}

type Props = {
  strategies: StrategyRow[]
  leagueAccounts: LeagueAccount[]
  onConfigure: (strategyId: string, configuration: StrategyConfiguration) => Promise<unknown>
  onRollback?: (strategyId: string, targetRevision: number, expectedRevision: number) => Promise<unknown>
}

const lifecycleLabels: Record<StrategyRow['lifecycle'], string> = {
  RESEARCH: '연구 중',
  SHADOW: '독립 검증 중',
  CHALLENGER: '도전자',
  ACTIVE: '현재 대표',
  QUARANTINED: '안전 격리',
  RETIRED: '퇴역·보존',
}

const governanceReasonLabels: Record<string, string> = {
  ACTIVE_GATES_HEALTHY: '두 평가 주기 연속 악화가 없어 현재 대표를 유지합니다.',
  USER_MANUAL_LOCK: '사용자가 설정을 고정해 자동 변경하지 않습니다.',
  RETIRED_REQUIRES_USER_RESEARCH: '퇴역 전략은 새 연구 검증 전에 자동 재활성화하지 않습니다.',
  LIVE_PUBLIC_SAMPLE_LT_30: '실제 공개시장 PAPER 표본이 30건보다 적습니다.',
  DSR_LT_0_80_OR_MISSING: '다중 실험 보정 결과가 없거나 기준에 미달합니다.',
  PBO_GT_0_50_OR_MISSING: '과적합 확률 검증이 없거나 기준에 미달합니다.',
  OOS_EXPECTANCY_LOWER_BOUND_NOT_POSITIVE: '미사용 기간의 비용 후 기대값 하한이 양수로 확인되지 않았습니다.',
  PARAMETER_ROBUSTNESS_NOT_PASSED: '주변 파라미터에서도 재현되는지 검증이 필요합니다.',
  COST_AFTER_DEGRADATION: '전체와 최근 OOS가 두 평가 주기 연속 비용 후 악화됐습니다.',
  DATA_LEAKAGE: '미래 데이터 누수가 감지돼 즉시 격리했습니다.',
  LEDGER_CONTAMINATION: '원장 무결성 문제가 감지돼 즉시 격리했습니다.',
  ABNORMAL_ORDER_LOOP: '비정상 PAPER 주문 루프가 감지돼 즉시 격리했습니다.',
}

function governanceReason(reason: string) {
  return governanceReasonLabels[reason] ?? reason
}

function evaluationTime(timestamp: number) {
  return timestamp > 0 ? new Date(timestamp).toLocaleString('ko-KR') : '시작 설정'
}

function number(value: string) {
  return Number(value || 0)
}

function monitorState(strategy: StrategyRow, accounts: LeagueAccount[]) {
  if (strategy.mode === 'OFF' || (!strategy.long_enabled && !strategy.short_enabled)) {
    return { tone: 'off', label: '꺼짐', detail: '설정에서 사용하지 않음' }
  }
  if (accounts.some((account) => account.faulted)) return { tone: 'fault', label: '확인 필요', detail: '전략 가상계좌 오류' }
  if (accounts.some((account) => account.paused)) return { tone: 'waiting', label: '안전 대기', detail: '새 진입만 잠시 차단' }
  const openPositions = accounts.reduce((total, account) => total + account.open_positions, 0)
  if (openPositions) return { tone: 'active', label: 'PAPER 진입 중', detail: `${openPositions}건 자동 관리` }
  if (strategy.qualified_paths > 0) return { tone: 'qualified', label: '진입 조건 감지', detail: `${strategy.qualified_paths}개 경로 체결 확인 중` }
  if (strategy.evaluated_paths === 0 || strategy.latest_status === 'WAITING_DATA') return { tone: 'waiting', label: '준비 중', detail: '공개시장 표본을 모으는 중' }
  const reasons = [...new Set(strategy.latest_reasons.map(strategyWaitReasonLabel))].slice(0, 2)
  return { tone: 'watching', label: '정상 감시 중', detail: reasons.join(' · ') || '진입 조건 대기' }
}

function ProfileDetails({ report, account }: { report: StrategyPerformance; account: LeagueAccount | undefined }) {
  const windows = ['recent_50', 'recent_100', 'recent_300'] as const
  return (
    <section className="profile-detail-block">
      <h3>{report.profile} 가상계좌</h3>
      <p className="profile-scope-note">자산·순손익은 이번 Run, 아래 통계는 현재 전략 버전의 공개시장 PAPER 기준입니다.</p>
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
        <div><dt>Omega · 거래당 Sortino</dt><dd>{formatRatio(report.omega_ratio)} · {formatRatio(report.sortino_ratio_per_trade)}</dd></div>
        <div><dt>비연환산 Calmar</dt><dd>{formatRatio(report.calmar_ratio_nonannualized)}<small> · 짧은 표본을 연환산하지 않음</small></dd></div>
        <div><dt>수수료 · 슬리피지</dt><dd>{formatUsdt(report.fees)} · {formatUsdt(report.slippage)}</dd></div>
        <div><dt>비용 부담</dt><dd>{formatPercentFraction(report.cost_burden)}</dd></div>
        <div><dt>최대 낙폭</dt><dd>{formatUsdt(report.maximum_drawdown)}</dd></div>
        <div><dt>양방향 거래대금</dt><dd>{formatUsdt(report.turnover_usdt)}<small> · 시작자산 대비 {formatRatio(report.turnover_ratio, 'x')}</small></dd></div>
        <div><dt>평균 MAE · MFE</dt><dd>{formatRatio(report.mae_r_mean, ' R')} · {formatRatio(report.mfe_r_mean, ' R')}</dd></div>
        <div><dt>보유 중앙 · p90</dt><dd>{formatDurationMs(report.median_hold_ms)} · {formatDurationMs(report.p90_hold_ms)}</dd></div>
        <div><dt>LONG · SHORT</dt><dd>{report.sides.LONG} · {report.sides.SHORT}</dd></div>
        <div><dt>종목 · 시장상태</dt><dd>{report.symbols.length}개 · {report.regime_count}개</dd></div>
        <div><dt>시장상태별 기여</dt><dd>{report.regime_contributions.length ? report.regime_contributions.map((row) => `${row.regime} ${formatUsdt(row.net_pnl, { signed: true })}`).join(' · ') : '표본 없음'}</dd></div>
        <div><dt>표본상태</dt><dd>{report.sample_status}</dd></div>
        <div><dt>과거 버전 제외</dt><dd>{report.excluded_prior_version_samples}건</dd></div>
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

export function StrategiesPage({ strategies, leagueAccounts, onConfigure, onRollback }: Props) {
  const [saving, setSaving] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const ordered = useMemo(() => orderedStrategies(strategies), [strategies])
  const selected = useMemo(
    () => strategies.find((strategy) => strategy.strategy_id === selectedId) ?? null,
    [selectedId, strategies],
  )
  const configure = useCallback(async (strategy: StrategyRow, configuration: Partial<StrategyConfiguration>) => {
    setSaving(strategy.strategy_id)
    try {
      await onConfigure(strategy.strategy_id, {
        mode: strategy.mode,
        long_enabled: strategy.long_enabled,
        short_enabled: strategy.short_enabled,
        expected_revision: strategy.settings_revision,
        ...configuration,
      })
    } finally {
      setSaving('')
    }
  }, [onConfigure])
  const closeDrawer = useCallback(() => setSelectedId(''), [])
  const accounts = selected ? leagueAccounts.filter((account) => account.strategy_id === selected.strategy_id) : []
  const monitorRows = ordered.map((strategy) => monitorState(
    strategy,
    leagueAccounts.filter((account) => account.strategy_id === strategy.strategy_id),
  ))
  const healthyCount = monitorRows.filter((row) => !['fault', 'off'].includes(row.tone)).length
  const faultCount = monitorRows.filter((row) => row.tone === 'fault').length
  const offCount = monitorRows.filter((row) => row.tone === 'off').length
  return (
    <section aria-labelledby="strategies-heading">
      <div className="page-heading"><div><p className="section-kicker">PAPER 전략</p><h2 id="strategies-heading">전략 설정</h2><p className="heading-help">각 전략이 실제로 평가 중인지와 지금 진입하지 않는 이유를 한 줄로 표시합니다. 조건이 맞지 않는 대기는 오류가 아닙니다.</p></div><span className={faultCount ? 'page-note negative' : 'page-note'}>{healthyCount}개 감시 · 검증 중지 {offCount}개 · 문제 {faultCount}개 · 실제 주문 0</span></div>
      {ordered.length === 0 ? <div className="panel empty-state"><b>전략 정보를 불러오는 중입니다.</b></div> : null}
      <section className="panel strategy-compact-panel"><div className="table-scroll"><table className="strategy-compact-table"><thead><tr><th>전략</th><th>현재 감시</th><th>사용 상태</th><th>방향</th><th>현재 PAPER</th><th>완료</th><th>승률</th><th>표본</th><th>상세</th></tr></thead><tbody>{ordered.map((strategy) => {
        const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === 'BASE')
        const pnl = account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0
        const winRate = account?.win_rate === null || account?.win_rate === undefined ? '표본 없음' : formatPercentFraction(account.win_rate)
        const isSaving = saving === strategy.strategy_id
        const monitor = monitorState(strategy, leagueAccounts.filter((item) => item.strategy_id === strategy.strategy_id))
        const changeMode = (mode: StrategyConfiguration['mode']) => {
          if (mode === strategy.mode) return
          if (window.confirm(`${strategy.short_name} 사용 상태를 ${modeLabels[mode]}(으)로 바꿀까요? 진행 중 PAPER는 기존 계획대로 관리됩니다.`)) {
            void configure(strategy, { mode })
          }
        }
        return <tr key={strategy.strategy_id} data-strategy-id={strategy.strategy_id}><td><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko} · {lifecycleLabels[strategy.lifecycle]}</small></td><td><span className={`strategy-monitor ${monitor.tone}`}>{monitor.label}</span><small>{monitor.detail} · {strategy.evaluated_paths}개 경로 확인</small></td><td><div className="strategy-inline-modes">{(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => <button type="button" aria-label={`${strategy.short_name} ${modeLabels[mode]}`} aria-pressed={strategy.mode === mode} disabled={isSaving} key={mode} onClick={() => changeMode(mode)}>{strategy.mode === mode && isSaving ? '저장 중' : modeLabels[mode]}</button>)}</div><small>{strategy.manual_lock ? '사용자 고정' : '자동 변경 가능'} · rev {strategy.settings_revision} · {strategy.changed_by}</small></td><td><div className="strategy-inline-directions"><button type="button" aria-pressed={strategy.long_enabled} disabled={isSaving} onClick={() => void configure(strategy, { long_enabled: !strategy.long_enabled })}>상승 {strategy.long_enabled ? '켜짐' : '꺼짐'}</button><button type="button" aria-pressed={strategy.short_enabled} disabled={isSaving} onClick={() => void configure(strategy, { short_enabled: !strategy.short_enabled })}>하락 {strategy.short_enabled ? '켜짐' : '꺼짐'}</button></div></td><td><span className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>{formatUsdt(pnl, { signed: true })}</span><small>자산 {formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</small></td><td>{account?.trade_count ?? 0}건<small>진행 {account?.open_positions ?? 0}건</small></td><td>{winRate}</td><td>{strategy.performance.BASE.sample_status}</td><td><button type="button" className="secondary-button" onClick={() => setSelectedId(strategy.strategy_id)}>자세히</button></td></tr>
      })}</tbody></table></div></section>
      <SideDrawer title={selected ? `${selected.short_name} · ${selected.display_name_ko}` : '전략 상세'} open={selected !== null} onClose={closeDrawer} label="전략 상세 정보">
        {selected ? <>
          <p className="drawer-subtitle"><b>{lifecycleLabels[selected.lifecycle]}</b> · {modeLabels[selected.mode]} · strategy_id {selected.strategy_id}</p>
          <section className="profile-detail-block">
            <h3>자동 평가 상태</h3>
            <dl className="drawer-detail-list">
              <div><dt>현재 대표</dt><dd>{selected.governance.champion_id ? strategyLabel(strategies.find((item) => item.strategy_id === selected.governance.champion_id), selected.governance.champion_id) : selected.lifecycle === 'ACTIVE' ? selected.short_name : '없음'}</dd></div>
              <div><dt>마지막 평가</dt><dd>{evaluationTime(selected.governance.last_evaluated_ts_ms)}</dd></div>
              <div><dt>검증 결론</dt><dd>{selected.governance.evidence_status === 'PROVEN' ? '검증됨' : '아직 검증 불충분'}</dd></div>
              <div><dt>다음 평가까지</dt><dd>{selected.governance.remaining_live_samples}건 · {selected.governance.remaining_days.toFixed(1)}일 더 필요</dd></div>
              <div><dt>현재 이유</dt><dd>{selected.governance.reason_codes.slice(0, 4).map(governanceReason).join(' ')}</dd></div>
              <div><dt>자동 변경</dt><dd>{selected.manual_lock ? '사용자 고정으로 차단' : selected.governance.automatic_action_allowed ? '검증된 전환 가능' : '현재 조건에서 변경 없음'}</dd></div>
            </dl>
            <details><summary>변경 이력</summary><ol>{selected.governance.change_history.map((row) => <li key={row.settings_revision}>rev {row.settings_revision} · {lifecycleLabels[row.lifecycle]} · {row.change_reason} · {evaluationTime(row.settings_updated_ts_ms)}</li>)}</ol></details>
            {onRollback && selected.governance.change_history.length > 1 ? <button type="button" className="secondary-button" onClick={() => {
              const previous = selected.governance.change_history.at(-2)
              if (previous && window.confirm(`${selected.short_name} 설정을 rev ${previous.settings_revision}로 복원할까요? 현재 기록은 삭제되지 않습니다.`)) void onRollback(selected.strategy_id, previous.settings_revision, selected.settings_revision)
            }}>직전 설정으로 복원</button> : null}
          </section>
          <ProfileDetails report={selected.performance.BASE} account={accounts.find((account) => account.profile === 'BASE')} />
          <ProfileDetails report={selected.performance.STRESS} account={accounts.find((account) => account.profile === 'STRESS')} />
        </> : null}
      </SideDrawer>
    </section>
  )
}
