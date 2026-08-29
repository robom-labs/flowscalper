// 전략의 핵심 상태는 짧은 표로, BASE·STRESS 세부 성과는 drawer로 분리한다.
import { useCallback, useMemo, useState } from 'react'
import { SideDrawer } from '../components/SideDrawer'
import { costProfileLabel, formatDurationMs, formatPercentFraction, formatRatio, formatUsdt } from '../format'
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
  analyticsReady?: boolean
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
  BASE_WIN_RATE_LT_0_70_OR_MISSING: '기본 비용 기준 승률이 70% 이상으로 확인되지 않았습니다.',
  STRESS_WIN_RATE_LT_0_70_OR_MISSING: '보수 비용 기준 승률이 70% 이상으로 확인되지 않았습니다.',
  BASE_WIN_RATE_LT_0_70_AFTER_MINIMUM_EVIDENCE: '충분한 기본 비용 표본에서 승률 70%에 못 미쳐 검증을 종료했습니다.',
  STRESS_WIN_RATE_LT_0_70_AFTER_MINIMUM_EVIDENCE: '충분한 보수 비용 표본에서 승률 70%에 못 미쳐 검증을 종료했습니다.',
  DSR_LT_0_80_OR_MISSING: '다중 실험 보정 결과가 없거나 기준에 미달합니다.',
  PBO_GT_0_50_OR_MISSING: '과적합 확률 검증이 없거나 기준에 미달합니다.',
  OOS_EXPECTANCY_LOWER_BOUND_NOT_POSITIVE: '미사용 기간의 비용 후 기대값 하한이 양수로 확인되지 않았습니다.',
  PARAMETER_ROBUSTNESS_NOT_PASSED: '주변 파라미터에서도 재현되는지 검증이 필요합니다.',
  COST_AFTER_DEGRADATION: '전체와 최근 OOS가 두 평가 주기 연속 비용 후 악화됐습니다.',
  WIN_RATE_BELOW_70_REPEATED: '전체와 최근 BASE·보수 비용 승률이 두 평가 주기 연속 70%에 못 미쳐 안전 격리했습니다.',
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

function milestoneTiming(value: number | null, sampleSize: number) {
  return value === null || sampleSize === 0
    ? `표본 없음 · ${sampleSize}건`
    : `${formatDurationMs(value)} · ${sampleSize}건`
}

function number(value: string) {
  return Number(value || 0)
}

function sampleStatusLabel(sampleSize: number, status: string) {
  if (sampleSize < 30) return `검증 자료 모으는 중 · ${sampleSize}/30건`
  if (status.includes('PROVEN') && !status.includes('NOT')) return '검증 기준 통과'
  if (status.includes('FAIL') || status.includes('REJECT')) return '검증 기준 미달'
  return '추가 검증 필요'
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

function ProfileDetails({ report, account, analyticsReady }: { report: StrategyPerformance; account: LeagueAccount | undefined; analyticsReady: boolean }) {
  const windows = ['recent_50', 'recent_100', 'recent_300'] as const
  return (
    <section className="profile-detail-block">
      <h3>{costProfileLabel(report.profile)} 가상계좌</h3>
      <p className="profile-scope-note">자산·순손익은 이번 실행, 완료 표본은 현재 전략 버전의 공개시장 모의거래 기준입니다.</p>
      {!analyticsReady ? <p className="profile-scope-note" role="status">과거 거래기록을 전략 버전별로 확인하고 있습니다. 완료되기 전에는 승률과 손익 통계를 표시하지 않습니다.</p> : null}
      <dl className="drawer-detail-list">
        <div><dt>현재 자산</dt><dd>{formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</dd></div>
        <div><dt>이번 실행 순손익</dt><dd>{formatUsdt(account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0, { signed: true })}</dd></div>
        <div><dt>완료 거래</dt><dd>{report.sample_size}건 <small>· 승 {report.wins} · 패 {report.losses} · 보합 {report.breakevens}</small></dd></div>
        <div><dt>승률</dt><dd>{report.win_rate === null ? '아직 표본 없음' : formatPercentFraction(report.win_rate)}<small>{report.win_rate_ci95 ? ` · 예상 범위 ${formatPercentFraction(report.win_rate_ci95.lower)}~${formatPercentFraction(report.win_rate_ci95.upper)}` : ''}</small></dd></div>
        <div><dt>거래당 기대값</dt><dd>{formatUsdt(report.expectancy_usdt)}</dd></div>
        <div><dt>비용</dt><dd>{formatUsdt(number(report.fees) + number(report.slippage))}<small>· 수수료와 가격차이 포함</small></dd></div>
        <div><dt>최대 낙폭</dt><dd>{formatUsdt(report.maximum_drawdown)}</dd></div>
        <div><dt>보유시간</dt><dd>{report.sample_size === 0 ? '표본 없음' : `보통 ${formatDurationMs(report.median_hold_ms)} · 긴 편 ${formatDurationMs(report.p90_hold_ms)}`}</dd></div>
        <div><dt>현재 판단</dt><dd>{sampleStatusLabel(report.sample_size, report.sample_status)}<small>· 30건 전에는 우열을 정하지 않음</small></dd></div>
      </dl>
      <details className="advanced-details"><summary>고급 통계 보기</summary>
        <dl className="drawer-detail-list">
          <div><dt>평균 이익 · 손실</dt><dd>{report.average_win_usdt === null ? '표본 없음' : formatUsdt(report.average_win_usdt)} · {report.average_loss_usdt === null ? '표본 없음' : formatUsdt(report.average_loss_usdt)}</dd></div>
          <div><dt>손익비 · Profit Factor</dt><dd>{formatRatio(report.payoff_ratio)} · {formatRatio(report.profit_factor)}</dd></div>
          <div><dt>기대값 R · bp</dt><dd>{formatRatio(report.expectancy_r, ' R')} · {formatRatio(report.expectancy_bps, ' bp')}</dd></div>
          <div><dt>Omega · 거래당 Sortino</dt><dd>{formatRatio(report.omega_ratio)} · {formatRatio(report.sortino_ratio_per_trade)}</dd></div>
          <div><dt>비연환산 Calmar</dt><dd>{formatRatio(report.calmar_ratio_nonannualized)}</dd></div>
          <div><dt>비용 부담</dt><dd>{formatPercentFraction(report.cost_burden)}</dd></div>
          <div><dt>양방향 거래대금</dt><dd>{formatUsdt(report.turnover_usdt)} · {formatRatio(report.turnover_ratio, 'x')}</dd></div>
          <div><dt>평균 불리·유리 이동</dt><dd>{formatRatio(report.mae_r_mean, ' R')} · {formatRatio(report.mfe_r_mean, ' R')}</dd></div>
          <div><dt>1차·2차 목표까지</dt><dd>{milestoneTiming(report.median_time_to_tp1_ms, report.tp1_sample_size)} · {milestoneTiming(report.median_time_to_tp2_ms, report.tp2_sample_size)}</dd></div>
          <div><dt>손절까지</dt><dd>{milestoneTiming(report.median_time_to_stop_ms, report.stop_sample_size)}</dd></div>
          <div><dt>상승·하락 방향</dt><dd>{report.sides.LONG}건 · {report.sides.SHORT}건</dd></div>
          <div><dt>종목·시장상태</dt><dd>{report.symbols.length}개 · {report.regime_count}개</dd></div>
          <div><dt>과거 버전 제외</dt><dd>{report.excluded_prior_version_samples}건</dd></div>
          <div><dt>기술 판단 코드</dt><dd>{report.sample_status} · {report.recommendation}</dd></div>
        </dl>
        <div className="window-summary">{windows.map((key) => {
          const value = report.windows[key]
          const size = typeof value?.sample_size === 'number' ? value.sample_size : 0
          return <span key={key}>{key.replace('recent_', '최근 ')} · {size}건</span>
        })}</div>
      </details>
    </section>
  )
}

export function StrategiesPage({ strategies, leagueAccounts, analyticsReady = true, onConfigure, onRollback }: Props) {
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
      <div className="page-heading"><div><p className="section-kicker">모의매매 전략</p><h2 id="strategies-heading">전략 설정</h2><p className="heading-help">각 전략이 지금 무엇을 하는지와 진입을 기다리는 이유를 한 줄로 표시합니다. 조건을 기다리는 것은 오류가 아닙니다.</p></div><span className={faultCount ? 'page-note negative' : 'page-note'}>{healthyCount}개 감시 · 검증 중지 {offCount}개 · 문제 {faultCount}개 · 실제 주문 0</span></div>
      {!analyticsReady ? <p className="profile-scope-note" role="status">과거 거래통계를 전략 버전별로 불러오는 중입니다. 준비 전 숫자는 순위나 승률로 사용하지 않습니다.</p> : null}
      {ordered.length === 0 ? <div className="panel empty-state"><b>전략 정보를 불러오는 중입니다.</b></div> : null}
      <section className="panel strategy-compact-panel"><div className="table-scroll"><table className="strategy-compact-table"><thead><tr><th>전략</th><th>지금 상태</th><th>사용 방식</th><th>거래 방향</th><th>이번 실행 결과</th><th>검증 결과</th><th>보기</th></tr></thead><tbody>{ordered.map((strategy) => {
        const account = leagueAccounts.find((item) => item.strategy_id === strategy.strategy_id && item.profile === 'BASE')
        const report = strategy.performance.BASE
        const pnl = account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0
        const winRate = !analyticsReady ? '불러오는 중' : report.win_rate === null ? '표본 없음' : formatPercentFraction(report.win_rate)
        const isSaving = saving === strategy.strategy_id
        const monitor = monitorState(strategy, leagueAccounts.filter((item) => item.strategy_id === strategy.strategy_id))
        const changeMode = (mode: StrategyConfiguration['mode']) => {
          if (mode === strategy.mode) return
          if (window.confirm(`${strategy.short_name} 사용 상태를 ${modeLabels[mode]}(으)로 바꿀까요? 진행 중 PAPER는 기존 계획대로 관리됩니다.`)) {
            void configure(strategy, { mode })
          }
        }
        return <tr key={strategy.strategy_id} data-strategy-id={strategy.strategy_id}>
          <td data-label="전략"><strong>{strategy.short_name}</strong><small>{strategy.display_name_ko} · {lifecycleLabels[strategy.lifecycle]}</small></td>
          <td data-label="지금 상태"><span className={`strategy-monitor ${monitor.tone}`}>{monitor.label}</span><small>{monitor.detail} · {strategy.evaluated_paths}개 조건 확인</small></td>
          <td data-label="사용 방식"><div className="strategy-inline-modes">{(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => <button type="button" aria-label={`${strategy.short_name} ${modeLabels[mode]}`} aria-pressed={strategy.mode === mode} disabled={isSaving || (strategy.policy_reactivation_locked && mode !== 'OFF')} key={mode} onClick={() => changeMode(mode)}>{strategy.mode === mode && isSaving ? '저장 중' : modeLabels[mode]}</button>)}</div><small>{strategy.policy_reactivation_locked ? '검증 실패로 다시 켤 수 없음' : strategy.manual_lock ? '사용자가 고정함' : '검증 결과에 따라 자동 관리'}</small></td>
          <td data-label="거래 방향"><div className="strategy-inline-directions"><button type="button" aria-pressed={strategy.long_enabled} disabled={isSaving || strategy.policy_reactivation_locked} onClick={() => void configure(strategy, { long_enabled: !strategy.long_enabled })}>상승 {strategy.long_enabled ? '켜짐' : '꺼짐'}</button><button type="button" aria-pressed={strategy.short_enabled} disabled={isSaving || strategy.policy_reactivation_locked} onClick={() => void configure(strategy, { short_enabled: !strategy.short_enabled })}>하락 {strategy.short_enabled ? '켜짐' : '꺼짐'}</button></div></td>
          <td data-label="이번 실행 결과"><span className={pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : ''}>{formatUsdt(pnl, { signed: true })}</span><small>현재 자산 {formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })} · 보유 {account?.open_positions ?? 0}건</small></td>
          <td data-label="검증 결과"><strong>{analyticsReady ? sampleStatusLabel(report.sample_size, report.sample_status) : '불러오는 중'}</strong><small>{analyticsReady ? `현재 승률 ${winRate}` : '통계를 확인하고 있습니다.'}</small></td>
          <td data-label="보기"><button type="button" className="secondary-button" onClick={() => setSelectedId(strategy.strategy_id)}>자세히</button></td>
        </tr>
      })}</tbody></table></div></section>
      <SideDrawer title={selected ? `${selected.short_name} · ${selected.display_name_ko}` : '전략 상세'} open={selected !== null} onClose={closeDrawer} label="전략 상세 정보">
        {selected ? <>
          <p className="drawer-subtitle"><b>{lifecycleLabels[selected.lifecycle]}</b> · {modeLabels[selected.mode]}</p>
          <section className="profile-detail-block">
            <h3>한눈에 보는 전략</h3>
            <dl className="drawer-detail-list">
              <div><dt>예상 보유</dt><dd>{formatDurationMs(selected.expected_holding_seconds[0] * 1_000)}~{formatDurationMs(selected.expected_holding_seconds[1] * 1_000)}</dd></div>
              <div><dt>이익 목표</dt><dd>1차 {selected.take_profit_1_r}R · 2차 {selected.take_profit_2_r}R</dd></div>
              <div><dt>최소 준비</dt><dd>{selected.minimum_warmup_ko}</dd></div>
              <div><dt>무엇을 노리나요?</dt><dd>{selected.entry_hypothesis_ko}</dd></div>
              <div><dt>가격·근거 동시 악화</dt><dd>{selected.edge_decay_policy_ko}</dd></div>
            </dl>
            <details className="advanced-details"><summary>고급 기술 정보</summary><dl className="drawer-detail-list"><div><dt>전략 코드</dt><dd>{selected.strategy_id}</dd></div><div><dt>전략 시간축</dt><dd>{selected.horizon_class}</dd></div><div><dt>신호 반감기</dt><dd>{selected.signal_half_life_seconds}초</dd></div><div><dt>사용 시간구간</dt><dd>{selected.required_timeframes.join(' · ')}</dd></div><div><dt>자동 관리 모델</dt><dd>{selected.exit_model} · 최대 {formatDurationMs(selected.max_hold_seconds * 1_000)}</dd></div><div><dt>비용 모델</dt><dd>{selected.cost_model_version}</dd></div><div><dt>전략 버전</dt><dd>{selected.strategy_version}</dd></div><div><dt>필요 데이터</dt><dd>{selected.required_market_data.join(' · ')}</dd></div><div><dt>반증 조건</dt><dd>{selected.falsification_conditions_ko.join(' · ')}</dd></div><div><dt>위험예산</dt><dd>{selected.risk_budget_rule_ko}</dd></div><div><dt>대상 범위</dt><dd>{selected.target_universe_ko} · {selected.supported_regimes.join(' · ')}</dd></div><div><dt>미래정보 방지</dt><dd>{selected.data_leakage_guards_ko.join(' · ')}</dd></div><div><dt>연구 근거</dt><dd>{selected.research_source_ids.join(' · ')}</dd></div><div><dt>현재 상태 코드</dt><dd>{selected.change_reason}</dd></div><div><dt>설정 개정</dt><dd>rev {selected.settings_revision} · {selected.changed_by}</dd></div></dl></details>
          </section>
          {selected.entry_rules_ko.length || selected.exit_rules_ko.length ? <section className="profile-detail-block">
            <h3>언제 진입하고 언제 나오나요?</h3>
            <dl className="drawer-detail-list">
              {selected.entry_rules_ko.length ? <div><dt>진입 조건</dt><dd>{selected.entry_rules_ko.join(' · ')}</dd></div> : null}
              {selected.exit_rules_ko.length ? <div><dt>종료 규칙</dt><dd>{selected.exit_rules_ko.join(' · ')}</dd></div> : null}
            </dl>
            <p className="profile-scope-note">아직 수익성이 입증되지 않은 독립 PAPER 검증 전략입니다. 공동계좌나 실제 주문에는 연결되지 않습니다.</p>
          </section> : null}
          <section className="profile-detail-block">
            <h3>자동 평가 상태</h3>
            <dl className="drawer-detail-list">
              <div><dt>공동계좌 현재 대표</dt><dd>{selected.governance.champion_id ? strategyLabel(strategies.find((item) => item.strategy_id === selected.governance.champion_id), selected.governance.champion_id) : selected.lifecycle === 'ACTIVE' ? selected.short_name : '없음'}</dd></div>
              <div><dt>마지막 평가</dt><dd>{evaluationTime(selected.governance.last_evaluated_ts_ms)}</dd></div>
              <div><dt>검증 결론</dt><dd>{selected.governance.evidence_status === 'PROVEN' ? '검증됨' : '아직 검증 불충분'}</dd></div>
              <div><dt>다음 평가까지</dt><dd>{selected.governance.remaining_live_samples}건 · {selected.governance.remaining_days.toFixed(1)}일 더 필요</dd></div>
              <div><dt>현재 이유</dt><dd>{selected.governance.reason_codes.slice(0, 4).map(governanceReason).join(' ')}</dd></div>
              <div><dt>자동 변경</dt><dd>{selected.manual_lock ? '사용자 고정으로 차단' : selected.governance.automatic_action_allowed ? '검증된 전환 가능' : '현재 조건에서 변경 없음'}</dd></div>
            </dl>
            <details><summary>변경 이력</summary><ol>{selected.governance.change_history.map((row) => <li key={row.transition_id}><strong>rev {row.response_revision}</strong> · {row.description_ko}<small>{row.previous_state} → {row.new_state} · {row.actor} · {row.cause_code} · {evaluationTime(row.occurred_ts_ms)}</small></li>)}</ol></details>
            {selected.policy_reactivation_locked ? <p className="profile-scope-note">비용후 검증으로 퇴역한 전략입니다. 과거 변경 기록은 보존되지만 새 연구 승인 전에는 복원할 수 없습니다.</p> : null}
            {onRollback && !selected.policy_reactivation_locked && selected.governance.change_history.length > 1 ? <button type="button" className="secondary-button" onClick={() => {
              const previous = selected.governance.change_history.at(-2)
              if (previous && window.confirm(`${selected.short_name} 설정을 rev ${previous.settings_revision}로 복원할까요? 현재 기록은 삭제되지 않습니다.`)) void onRollback(selected.strategy_id, previous.settings_revision, selected.settings_revision)
            }}>직전 설정으로 복원</button> : null}
          </section>
          <ProfileDetails report={selected.performance.BASE} account={accounts.find((account) => account.profile === 'BASE')} analyticsReady={analyticsReady} />
          <ProfileDetails report={selected.performance.STRESS} account={accounts.find((account) => account.profile === 'STRESS')} analyticsReady={analyticsReady} />
        </> : null}
      </SideDrawer>
    </section>
  )
}
