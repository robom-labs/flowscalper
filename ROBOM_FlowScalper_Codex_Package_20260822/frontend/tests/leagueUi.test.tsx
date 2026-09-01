// Registry가 늘어나도 독립계좌와 쉬운 전략 설정이 동적으로 표시되는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { PositionList } from '../src/components/PositionList'
import { StrategyPerformancePanel } from '../src/components/StrategyPerformancePanel'
import { StrategiesPage } from '../src/pages/StrategiesPage'
import { StrategySymbolPanel } from '../src/components/StrategySymbolPanel'
import { formatKstDateTime, formatKstTime } from '../src/time'
import type { HistoryRow, LeaguePosition, V9ResearchCandidate } from '../src/types'
import { dashboardFixture, leagueAccounts, strategies } from './fixtures'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function openStrategySettings(strategyId: string) {
  const row = document.querySelector(`[data-strategy-id="${strategyId}"]`)
  if (!(row instanceof HTMLElement)) throw new Error(`strategy row missing: ${strategyId}`)
  fireEvent.click(within(row).getByRole('button', { name: '자세히·설정' }))
}

test('shows only current non-retired strategies with trust-first ranking evidence', () => {
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} researchDetails controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(10)
  expect(document.querySelectorAll('.strategy-inline-modes button')).toHaveLength(0)
  expect(screen.queryByText('기록만 하기')).not.toBeInTheDocument()
  expect(screen.getByRole('region', { name: '전략 모의평가 요약' })).toHaveTextContent(/방향 진입 후보 ON10/)
  expect(screen.getByRole('region', { name: '전략 모의평가 요약' })).toHaveTextContent(/표시 중인 family 대표10/)
  expect(screen.getByRole('region', { name: '전략 모의평가 요약' })).toHaveTextContent(/전체 등록15/)
  expect(screen.getByText(/현재·도전자를 합친 방향 진입 후보 10개만 독립 SHADOW 모의평가 ON/)).toBeInTheDocument()
  expect(screen.getAllByText('준비 중')).toHaveLength(10)
  expect(document.querySelectorAll('.strategy-monitor.off')).toHaveLength(0)
  expect(document.querySelector('[data-strategy-id="LSA_REVERSAL_V1"]')).not.toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '신뢰승률' })).toHaveAttribute('aria-sort', 'descending')
  expect(screen.getByRole('columnheader', { name: '고유 거래' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '기대값' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '순손익' })).toBeInTheDocument()
  expect(screen.getByRole('group', { name: '성과 비용 기준' })).toBeInTheDocument()
  expect(screen.getByText(/30건 미만 승률은 참고값/)).toBeInTheDocument()

  openStrategySettings(strategies[1].strategy_id)
  const detailDialog = screen.getByRole('dialog', { name: '전략 상세 정보' })
  expect(within(detailDialog).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
    '지금 상태',
    '진입조건',
    '청산',
    '성과',
    '출처',
    '이전 버전',
  ])
  expect(screen.getByRole('heading', { name: '자동 평가 상태' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '한눈에 보는 전략' })).toBeInTheDocument()
  expect(screen.getByText('10초~3분')).toBeInTheDocument()
  expect(screen.getByText('고급 기술 정보')).toBeInTheDocument()
  expect(screen.getByText('TOP_OF_BOOK_BASE13_STRESS25_V1')).not.toBeVisible()
  expect(screen.getByText('필요 데이터')).toBeInTheDocument()
  expect(screen.getByText('최소 준비')).toBeInTheDocument()
  expect(screen.getByText('무엇을 노리나요?')).toBeInTheDocument()
  expect(screen.getByText('반증 조건')).toBeInTheDocument()
  expect(screen.getByText('종료 원칙')).toBeInTheDocument()
  expect(screen.getByText('위험예산')).toBeInTheDocument()
  expect(screen.getByText('대상 범위')).toBeInTheDocument()
  expect(screen.getByText('미래정보 방지')).toBeInTheDocument()
  expect(screen.getByText('현재 상태 코드')).not.toBeVisible()
  expect(screen.getByText('아직 검증 불충분')).toBeInTheDocument()

  fireEvent.click(within(detailDialog).getByRole('tab', { name: '성과' }))
  expect(within(detailDialog).getByRole('heading', { name: '기본 비용 가상계좌' })).toBeInTheDocument()
  expect(within(detailDialog).queryByRole('heading', { name: '보수 비용 가상계좌' })).not.toBeInTheDocument()
  expect(within(detailDialog).getAllByText(/현재 자산/)).toHaveLength(1)
  expect(within(detailDialog).getAllByText(/현재 전략 버전의 공개시장 모의거래 기준/)).toHaveLength(1)
  expect(within(detailDialog).getAllByText('과거 버전 제외')).toHaveLength(1)
  expect(within(detailDialog).getAllByText('고급 통계 보기')).toHaveLength(1)
  fireEvent.click(within(detailDialog).getByRole('button', { name: '보수 비용' }))
  expect(within(detailDialog).getByRole('heading', { name: '보수 비용 가상계좌' })).toBeInTheDocument()
  expect(within(detailDialog).queryByRole('heading', { name: '기본 비용 가상계좌' })).not.toBeInTheDocument()

  fireEvent.click(within(detailDialog).getByRole('tab', { name: '출처' }))
  expect(within(detailDialog).getByRole('heading', { name: '출처' })).toBeInTheDocument()

  fireEvent.click(screen.getAllByRole('button', { name: '전략 상세 정보 닫기' })[0])
  openStrategySettings(strategies[2].strategy_id)
  expect(screen.getByRole('button', { name: 'VWAP 소진 모의평가 켜기' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: '상승 켜짐' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getAllByRole('button', { name: '전략 상세 정보 닫기' })[0])
  openStrategySettings(strategies[13].strategy_id)
  expect(screen.getByText('2시간~18시간')).toBeInTheDocument()
  fireEvent.click(within(screen.getByRole('dialog', { name: '전략 상세 정보' })).getByRole('tab', { name: '청산' }))
  expect(screen.getByText(/TP1 1.6R·40%/)).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: '전략 상세 정보 닫기' })[0])
  openStrategySettings(strategies[11].strategy_id)
  expect(screen.getByText('30분~8시간')).toBeInTheDocument()
  expect(screen.getByText(/시간청산 없이 TP1·TP2·구조 손절로 결판/)).toBeInTheDocument()
})

test('sorts eligible strategies by Wilson lower bound and keeps sparse samples out of rank', () => {
  const evidence = new Map([
    [strategies[1].strategy_id, { sampleSize: 31, overallUnique: 31, profileUnique: 31, wins: 24, losses: 7, winRate: '0.774194', lower: '0.6' }],
    [strategies[2].strategy_id, { sampleSize: 32, overallUnique: 32, profileUnique: 32, wins: 22, losses: 10, winRate: '0.6875', lower: '0.5' }],
    [strategies[5].strategy_id, { sampleSize: 30, overallUnique: 30, profileUnique: 30, wins: 18, losses: 12, winRate: '0.6', lower: '0.4' }],
    [strategies[6].strategy_id, { sampleSize: 29, overallUnique: 30, profileUnique: 29, wins: 28, losses: 1, winRate: '0.965517', lower: '0.9' }],
  ])
  const rows = strategies.map((strategy) => {
    const value = evidence.get(strategy.strategy_id)
    return value ? {
      ...strategy,
      performance: {
        ...strategy.performance,
        BASE: { ...strategy.performance.BASE, sample_size: value.sampleSize, unique_opportunity_count: value.overallUnique, profile_unique_opportunity_count: value.profileUnique, wins: value.wins, losses: value.losses, win_rate: value.winRate, win_rate_ci95: { lower: value.lower, upper: '0.99' } },
      },
    } : strategy
  })
  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  const visibleIds = () => [...document.querySelectorAll<HTMLTableRowElement>('.strategy-compact-table tbody tr')].map((row) => row.dataset.strategyId)

  expect(screen.getByRole('columnheader', { name: '신뢰승률' })).toHaveAttribute('aria-sort', 'descending')
  expect(visibleIds().slice(0, 3)).toEqual([strategies[1].strategy_id, strategies[2].strategy_id, strategies[5].strategy_id])
  expect(visibleIds().indexOf(strategies[6].strategy_id)).toBeGreaterThan(2)
  fireEvent.click(screen.getByRole('button', { name: /신뢰승률 정렬/ }))
  expect(screen.getByRole('columnheader', { name: '신뢰승률' })).toHaveAttribute('aria-sort', 'ascending')
  expect(visibleIds().slice(0, 3)).toEqual([strategies[5].strategy_id, strategies[2].strategy_id, strategies[1].strategy_id])
  fireEvent.click(screen.getByRole('button', { name: /신뢰승률 정렬/ }))
  expect(screen.getByRole('columnheader', { name: '신뢰승률' })).toHaveAttribute('aria-sort', 'descending')
  expect(visibleIds().slice(0, 3)).toEqual([strategies[1].strategy_id, strategies[2].strategy_id, strategies[5].strategy_id])
  expect(visibleIds().at(-1)).toBe(strategies.at(-1)?.strategy_id)
})

test('shows lifecycle evidence and restores the prior revision without deleting history', async () => {
  const current = strategies[2]
  const revisionZero = {
    strategy_id: current.strategy_id,
    mode: 'SHADOW' as const,
    lifecycle: 'SHADOW' as const,
    long_enabled: true,
    short_enabled: true,
    settings_revision: 0,
    manual_lock: false,
    changed_by: 'MIGRATION' as const,
    change_reason: 'SAFE_DEFAULT',
    settings_updated_ts_ms: 0,
    policy_reactivation_locked: false,
    transition_id: `strategy-setting-run-fixture-${current.strategy_id}-rev-0`,
    previous_state: 'NONE',
    new_state: 'SHADOW|SHADOW|LONG=ON|SHORT=ON|MANUAL_LOCK=OFF',
    occurred_ts_ms: 0,
    cause: 'SAFE_DEFAULT',
    cause_code: 'SAFE_DEFAULT',
    description_ko: '전략 초기 설정을 적용했습니다.',
    actor: 'RECOVERY' as const,
    run_id: 'run-fixture',
    account_id: null,
    symbol: null,
    request_revision: 0,
    response_revision: 0,
    reversible: true,
  }
  const revisionOne = {
    ...revisionZero,
    short_enabled: false,
    settings_revision: 1,
    manual_lock: true,
    changed_by: 'USER_UI' as const,
    change_reason: 'USER_CONFIGURATION',
    settings_updated_ts_ms: 1_759_888_000_000,
    transition_id: `strategy-setting-run-fixture-${current.strategy_id}-rev-1`,
    previous_state: revisionZero.new_state,
    new_state: 'SHADOW|SHADOW|LONG=ON|SHORT=OFF|MANUAL_LOCK=ON',
    occurred_ts_ms: 1_759_888_000_000,
    cause: 'USER_CONFIGURATION',
    cause_code: 'USER_CONFIGURATION',
    description_ko: '전략 설정을 SHADOW 상태로 변경했습니다.',
    actor: 'USER_UI' as const,
    request_revision: 0,
    response_revision: 1,
  }
  const rows = strategies.map((strategy) => strategy.strategy_id === current.strategy_id ? {
    ...strategy,
    short_enabled: false,
    settings_revision: 1,
    manual_lock: true,
    governance: {
      ...strategy.governance,
      settings_revision: 1,
      manual_lock: true,
      change_history: [revisionZero, revisionOne],
    },
  } : strategy)
  const onRollback = vi.fn(async () => undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} researchDetails controlsEnabled onConfigure={vi.fn(async () => undefined)} onRollback={onRollback} />)

  openStrategySettings(current.strategy_id)
  const dialog = screen.getByRole('dialog', { name: '전략 상세 정보' })
  const history = dialog.querySelector('ol')
  if (!history) throw new Error('strategy change history missing')
  expect(within(history).getByText(/rev 0/)).toBeInTheDocument()
  expect(within(history).getByText(/rev 1/)).toBeInTheDocument()
  expect(within(history).getByText(/전략 설정을 SHADOW 상태로 변경/)).toBeInTheDocument()
  expect(within(history).getByText(/SHADOW\|SHADOW\|LONG=ON\|SHORT=ON.*USER_UI/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '직전 설정으로 복원' }))

  await waitFor(() => expect(onRollback).toHaveBeenCalledWith(
    current.strategy_id,
    0,
    1,
  ))
})

test('uses the backend retirement reason without a frontend phrase map', () => {
  const rows = strategies.map((strategy, index) => index === 1 ? {
    ...strategy,
    reason_code: 'BASE_WIN_RATE_LT_0_70_AFTER_MINIMUM_EVIDENCE',
    reason_ko: '기본 비용의 충분한 표본에서 승률 기준을 통과하지 못했습니다.',
    governance: {
      ...strategy.governance,
      recommended_lifecycle: 'RETIRED' as const,
      reason_codes: ['BASE_WIN_RATE_LT_0_70_AFTER_MINIMUM_EVIDENCE'],
    },
  } : strategy)

  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} researchDetails controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  openStrategySettings(strategies[1].strategy_id)

  expect(screen.getByText('기본 비용의 충분한 표본에서 승률 기준을 통과하지 못했습니다.')).toBeInTheDocument()
})

test('blocks policy-retired reactivation but keeps the ordinary user OFF family control reversible', () => {
  const userOffId = 'VWAP_EXHAUSTION_REVERSION_V1'
  const rows = strategies.map((strategy) => strategy.strategy_id === userOffId ? {
    ...strategy,
    family_id: 'EXHAUSTION_REVERSION',
    mode: 'OFF' as const,
    lifecycle: 'SHADOW' as const,
    policy_reactivation_locked: false,
  } : strategy)
  render(<StrategiesPage
    strategies={rows}
    leagueAccounts={leagueAccounts}
    controlsEnabled
    onConfigure={vi.fn(async () => undefined)}
    onConfigureFamilyResearch={vi.fn(async (familyId) => ({ family_id: familyId, variants: [] }))}
  />)

  expect(document.querySelector('[data-strategy-id="LSA_REVERSAL_V1"]')).not.toBeInTheDocument()
  openStrategySettings(userOffId)
  const reversible = screen.getByRole('button', { name: 'VWAP 소진 모의평가 켜기' })
  expect(reversible).toBeEnabled()
  expect(reversible).toHaveAttribute('aria-pressed', 'false')
})

test('shows backend reason text and never exposes raw governor codes', () => {
  const rows = strategies.map((strategy, index) => index === 11 ? {
    ...strategy,
    reason_code: 'BASE_SAMPLE_LT_30',
    reason_ko: '기본 비용의 고유 진입기회가 30건보다 적어 더 모으고 있습니다.',
    governance: {
      ...strategy.governance,
      reason_codes: [
        'BASE_SAMPLE_LT_30',
        'STRESS_SAMPLE_LT_30',
        'BASE_EXPECTANCY_NOT_POSITIVE',
        'STRESS_EXPECTANCY_NOT_POSITIVE',
      ],
    },
  } : strategy)

  render(<StrategiesPage strategies={rows} leagueAccounts={leagueAccounts} researchDetails controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  openStrategySettings(strategies[11].strategy_id)

  expect(screen.getByText(/기본 비용의 고유 진입기회가 30건보다 적어/)).toBeInTheDocument()
  expect(screen.getByText(/추가 검증 조건을 확인하고 있습니다/)).toBeInTheDocument()
  expect(screen.queryByText(/BASE_SAMPLE_LT_30/)).not.toBeInTheDocument()
})

test('never offers direct shared-capital activation and keeps family research separate from direction settings', () => {
  const onConfigure = vi.fn(async () => undefined)
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} controlsEnabled onConfigure={onConfigure} />)

  openStrategySettings('CBR_CONTINUATION_V1')
  expect(screen.getByRole('button', { name: 'CBR 돌파 모의평가 켜기' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'CBR 돌파 모의평가 끄기' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.queryByRole('button', { name: /CBR 돌파.*공동/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '상승 켜짐' })).toBeEnabled()
  expect(onConfigure).not.toHaveBeenCalled()
})

test('counts enabled challenger variants without adding them to the current family table', () => {
  const source = strategies[0]
  const challenger = {
    ...source,
    strategy_id: `${source.strategy_id}_CHALLENGER`,
    variant_id: `${source.strategy_id}_CHALLENGER`,
    variant_label_ko: '독립 검증 도전자',
    is_current_variant: false,
    user_visible_by_default: false,
    final_ranking_eligible: false,
    lifecycle: 'SHADOW' as const,
    mode: 'SHADOW' as const,
  }

  render(<StrategiesPage strategies={[...strategies, challenger]} leagueAccounts={leagueAccounts} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)

  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(10)
  const summary = screen.getByRole('region', { name: '전략 모의평가 요약' })
  expect(summary).toHaveTextContent(/방향 진입 후보 ON11/)
  expect(summary).toHaveTextContent(/표시 중인 family 대표10/)
  expect(summary).toHaveTextContent(/전체 등록16/)
})

test('separates the existing inventory from all 12 V9 tracking-only items', () => {
  const data = dashboardFixture()
  const candidateSpecs: Array<Pick<V9ResearchCandidate, 'candidate_id' | 'label_ko' | 'role' | 'family_id' | 'readiness'>> = [
    { candidate_id: 'DC_OVERSHOOT_CONTINUATION_V1', label_ko: 'DC Overshoot 추세 지속', role: 'ENTRY', family_id: 'BREAKOUT_RUNNER', readiness: 'PARTIAL_SOURCE_NOT_CONNECTED' },
    { candidate_id: 'DC_OVERSHOOT_EXHAUSTION_REVERSAL_V1', label_ko: 'DC Overshoot 소진 반전', role: 'ENTRY', family_id: 'EXHAUSTION_REVERSION', readiness: 'PARTIAL_SOURCE_NOT_CONNECTED' },
    { candidate_id: 'COPULA_COINTEGRATED_PAIRS_1H_V2', label_ko: 'Copula 비선형 시장중립 Pairs', role: 'MARKET_NEUTRAL_MULTI_LEG', family_id: 'MARKET_NEUTRAL', readiness: 'BLOCKED_ENGINE' },
    { candidate_id: 'SEMIVARIANCE_MOMENTUM_REVERSAL_ROUTER_V1', label_ko: '상승·하락 Semivariance Router', role: 'ROUTER', family_id: null, readiness: 'PARTIAL_SOURCE_NOT_CONNECTED' },
    { candidate_id: 'DOWNSIDE_SEMIVARIANCE_RISK_OVERLAY_V1', label_ko: '하방 Semivariance 위험축소', role: 'RISK_OVERLAY', family_id: null, readiness: 'PARTIAL_SOURCE_NOT_CONNECTED' },
    { candidate_id: 'HYSTERESIS_SETUP_GATE_V1', label_ko: 'Setup Hysteresis Gate', role: 'FILTER', family_id: null, readiness: 'SOURCE_IMPLEMENTED_NOT_CONNECTED' },
    { candidate_id: 'EVIDENCE_FRESHNESS_GATE_V1', label_ko: '최근 근거 신선도 Gate', role: 'SELECTION', family_id: null, readiness: 'SOURCE_IMPLEMENTED_NOT_CONNECTED' },
    { candidate_id: 'HIERARCHICAL_PERFORMANCE_SHRINKAGE_V1', label_ko: '계층적 성과 보정', role: 'STATISTICS', family_id: null, readiness: 'SOURCE_IMPLEMENTED_NOT_CONNECTED' },
    { candidate_id: 'BATCH_FDR_HARVEY_LIU_V1', label_ko: 'Batch FDR 검증', role: 'STATISTICS', family_id: null, readiness: 'BLOCKED_PREREQUISITE' },
    { candidate_id: 'ANYTIME_EPROCESS_V1', label_ko: 'Anytime E-process', role: 'STATISTICS', family_id: null, readiness: 'SOURCE_IMPLEMENTED_NOT_CONNECTED' },
    { candidate_id: 'E_BH_STRATEGY_SELECTION_V1', label_ko: 'e-BH 전략 선별', role: 'SELECTION', family_id: null, readiness: 'SOURCE_IMPLEMENTED_NOT_CONNECTED' },
    { candidate_id: 'PARETO_ROBUST_SET_V1', label_ko: 'Pareto 강건 후보집합', role: 'SELECTION', family_id: null, readiness: 'SOURCE_IMPLEMENTED_NOT_CONNECTED' },
  ]
  const v9Candidates: V9ResearchCandidate[] = candidateSpecs.map((candidate, index) => ({
    ...candidate,
    prerequisite_capability_ids: [`V9.${candidate.candidate_id}`],
    source_ids: [`SRC-V9-FIXTURE-${index + 1}`],
    monitoring_enabled: true,
    entry_enabled: false,
    active_enabled: false,
    runtime_entry_registered: false,
    can_increase_risk: false,
    paper_only: true,
    counts_as_direction_strategy: candidate.role === 'ENTRY',
    counts_as_market_neutral_strategy: candidate.role === 'MARKET_NEUTRAL_MULTI_LEG',
  }))
  data.strategies = [
    { ...strategies[2], family_id: 'EXHAUSTION_REVERSION', role: 'ENTRY', is_current_variant: true, user_visible_by_default: true },
    { ...strategies[11], family_id: 'TREND_PULLBACK', role: 'ENTRY', is_current_variant: true, user_visible_by_default: true },
    { ...strategies[13], family_id: 'BREAKOUT_RUNNER', role: 'ENTRY', is_current_variant: true, user_visible_by_default: true },
  ]
  const visibleIds = new Set(data.strategies.map((strategy) => strategy.strategy_id))
  data.league_accounts = data.league_accounts.filter((account) => visibleIds.has(account.strategy_id))
  data.strategy_family_catalog = {
    schema_version: 1,
    families: [],
    inventory: {
      schema: 'flowscalper.strategy_inventory.v1',
      registered_catalog_item_count: 16,
      runtime_registry_variant_count: 15,
      enabled_directional_entry_candidate_count: 6,
      current_family_entry_representative_count: 3,
      inactive_history_runtime_variant_count: 9,
      catalog_virtual_filter_count: 1,
      active_directional_entry_count: 0,
    },
    paper_only: true,
    real_orders_enabled: false,
    auth_required: false,
    private_api_enabled: false,
    api_key_enabled: false,
    wallet_enabled: false,
    runtime_ai_order_decision_enabled: false,
    funding_readiness: 'NOT_READY',
    v9_research: {
      schema: 'flowscalper.v9_candidate_registry.v1',
      status: 'MONITORING_ON_ENTRY_BLOCKED',
      source_commit: 'a'.repeat(40),
      candidate_count: 12,
      monitoring_on_count: 12,
      direction_strategy_count: 2,
      market_neutral_strategy_count: 1,
      runtime_entry_registered_count: 0,
      active_count: 0,
      entry_enabled_count: 0,
      candidates: v9Candidates,
      manifest_sha256: 'b'.repeat(64),
      paper_only: true,
      real_orders_enabled: false,
      auth_required: false,
      private_api_enabled: false,
      api_key_enabled: false,
      wallet_enabled: false,
      runtime_ai_order_decision_enabled: false,
      funding_readiness: 'NOT_READY',
    },
  }

  render(<StrategiesPage data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)

  const summary = screen.getByRole('region', { name: '전략 모의평가 요약' })
  expect(summary).toHaveTextContent(/방향 진입 후보 ON6/)
  expect(summary).toHaveTextContent(/기존 보존·중지9/)
  expect(summary).toHaveTextContent(/표시 중인 family 대표3/)
  expect(summary).toHaveTextContent(/기존 전체 등록16/)
  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(3)

  const panel = screen.getByRole('region', { name: 'V9 연구 추적 상태' })
  expect(within(panel).getByText('연구 추적 ON 12/12')).toBeInTheDocument()
  expect(within(panel).getByText(/새 방향 전략 2개 · 새 시장중립 1개 · PAPER 진입 활성 0개/)).toBeInTheDocument()
  expect(within(panel).getByText(/DC Overshoot 추세 지속/)).toBeInTheDocument()
  expect(within(panel).getAllByText(/방향 전략 · 핵심 소스 일부 구현 · 진입 미연결/)).toHaveLength(2)
  expect(within(panel).getByText(/시장중립 전략 · 실행 엔진 대기/)).toBeInTheDocument()
  expect(within(panel).getByText(/필터 · 소스 구현 · 진입 미연결/)).toBeInTheDocument()
  expect(within(panel).getByText(/통계 검증 · 선행 검증 대기/)).toBeInTheDocument()
  expect(within(panel).getAllByText('추적 ON')).toHaveLength(12)
  expect(within(panel).getAllByText('검증 전 진입 차단')).toHaveLength(12)
  expect(within(panel).getAllByText('등록 출처 1개')).toHaveLength(12)
  expect(within(panel).getByText(/추적 ON은 읽기 전용 연구 상태이며 PAPER 진입 스위치가 아닙니다/)).toBeInTheDocument()
  expect(panel.querySelectorAll('.v9-research-grid article')).toHaveLength(12)
})

test('distinguishes healthy condition waiting, open PAPER management and faults', () => {
  const rows = strategies.map((strategy, index) => ({
    ...strategy,
    evaluated_paths: index === 1 || index === 5 ? 24 : strategy.evaluated_paths,
    latest_status: index === 1 || index === 5 ? 'REJECTED' : strategy.latest_status,
    latest_reasons: index === 1 || index === 5 ? ['AGGRESSOR_FLOW_NOT_ALIGNED', 'QUEUE_ALIGNMENT_NOT_PERSISTENT'] : strategy.latest_reasons,
  }))
  const accounts = leagueAccounts.map((account) => ({ ...account }))
  const firstBase = accounts.find((account) => account.strategy_id === rows[1].strategy_id && account.profile === 'BASE')
  const secondStress = accounts.find((account) => account.strategy_id === rows[2].strategy_id && account.profile === 'STRESS')
  if (!firstBase || !secondStress) throw new Error('strategy fixture account missing')
  firstBase.open_positions = 1
  secondStress.faulted = true

  render(<StrategiesPage strategies={rows} leagueAccounts={accounts} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)

  expect(screen.getByText('PAPER 진입 중')).toBeInTheDocument()
  expect(screen.getByText('확인 필요')).toBeInTheDocument()
  expect(screen.getByText('조건 미충족')).toBeInTheDocument()
  const summary = screen.getByRole('region', { name: '전략 모의평가 요약' })
  expect(summary).toHaveTextContent(/방향 진입 후보 ON10/)
  expect(summary).toHaveTextContent(/진행 포지션1/)
  expect(screen.getByText(/1건 자동 관리/)).toBeInTheDocument()
})

const basePosition: LeaguePosition = {
  trade_id: 'trade-base',
  candidate_id: 'candidate-base',
  account_id: `${strategies[0].strategy_id}:BASE`,
  strategy_id: strategies[0].strategy_id,
  profile: 'BASE',
  symbol: 'BTCUSDT',
  side: 'LONG',
  signal_time: 1,
  opened_ts_ms: 1,
  actual_entry: '100',
  current_mark: '101',
  initial_stop: '99',
  current_stop: '99.5',
  TP1: '102',
  TP2: '103',
  original_quantity: '1',
  remaining_quantity: '1',
  notional: '100',
  effective_leverage: '0.1',
  gross_pnl: '1',
  fees: '0.1',
  slippage: '0.1',
  net_pnl: '0.8',
  elapsed_seconds: 30,
  exit_style: 'TWO_TARGET',
  management_reason: '진입 근거 유지',
}

test('shows current entry and the latest completed TP or stop timeline in strategy detail', () => {
  const data = dashboardFixture()
  const strategy = data.strategies[1]
  const openedTsMs = 1_759_888_000_000
  const completedEntryTsMs = openedTsMs - 300_000
  const completedExitTsMs = completedEntryTsMs + 120_000
  const openPosition: LeaguePosition = {
    ...basePosition,
    trade_id: 'trade-strategy-current',
    candidate_id: 'candidate-strategy-current',
    account_id: `${strategy.strategy_id}:BASE`,
    strategy_id: strategy.strategy_id,
    opened_ts_ms: openedTsMs,
    elapsed_seconds: 90,
  }
  const completed: HistoryRow = {
    run_id: 'run-fixture',
    trade_id: 'trade-strategy-completed',
    opportunity_id: 'opportunity-strategy-completed',
    symbol: 'ETHUSDT',
    strategy: strategy.strategy_id,
    side: 'LONG',
    entry: '100',
    exit: '99',
    entry_ts_ms: completedEntryTsMs,
    exit_ts_ms: completedExitTsMs,
    initial_stop: '99',
    take_profit: '103',
    take_profit_1: '101.5',
    take_profit_2: '103',
    tp1_hit_ts_ms: completedEntryTsMs + 45_000,
    tp2_hit_ts_ms: null,
    time_to_tp1_ms: 45_000,
    time_to_tp2_ms: null,
    time_to_stop_ms: 120_000,
    quantity: '1',
    exit_reason: 'STOP',
    gross_pnl: '-1',
    fees: '0.1',
    slippage: '0.05',
    net_pnl: '-1.15',
    holding_ms: 120_000,
    holding_seconds: 120,
    profile: 'BASE',
    sample_type: 'LIVE_PUBLIC',
    account_scope: 'LEAGUE',
    account_id: `${strategy.strategy_id}:BASE`,
    strategy_version: strategy.strategy_version,
  }
  data.league_positions = [openPosition]
  data.history = [completed]
  const account = data.league_accounts.find((item) => (
    item.strategy_id === strategy.strategy_id && item.profile === 'BASE'
  ))
  if (!account) throw new Error('strategy BASE account missing')
  account.open_positions = 1

  render(<StrategiesPage data={data} history={data.history} strategies={data.strategies} leagueAccounts={data.league_accounts} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)

  const row = document.querySelector(`[data-strategy-id="${strategy.strategy_id}"]`)
  if (!(row instanceof HTMLElement)) throw new Error('strategy row missing')
  expect(row).toHaveTextContent(`${formatKstTime(openedTsMs)} 진입 · 1분 30초 보유`)
  fireEvent.click(within(row).getByRole('button', { name: '자세히·설정' }))

  const detail = screen.getByRole('dialog', { name: '전략 상세 정보' })
  const activity = within(detail).getByRole('heading', { name: '현재와 최근 거래' }).closest('section')
  if (!(activity instanceof HTMLElement)) throw new Error('strategy activity section missing')
  expect(activity).toHaveTextContent('현재 PAPER 보유 중')
  expect(activity).toHaveTextContent(formatKstDateTime(openedTsMs))
  expect(activity).toHaveTextContent('1차 102 · 2차 103 · 손절 99.5')
  expect(activity).toHaveTextContent('가장 최근 완료')
  expect(activity).toHaveTextContent(formatKstDateTime(completedEntryTsMs))
  expect(activity).toHaveTextContent(formatKstDateTime(completedExitTsMs))
  expect(activity).toHaveTextContent(`1차 ${formatKstTime(completedEntryTsMs + 45_000)} · 진입 후 45초`)
  expect(activity).toHaveTextContent('2차 미도달')
  expect(activity).toHaveTextContent(`손절 ${formatKstTime(completedExitTsMs)} · 진입 후 2분`)
})

test('uses the basic-cost account as the default open-trade filter and can reveal conservative costs', () => {
  const stressPosition = { ...basePosition, trade_id: 'trade-stress', candidate_id: 'candidate-stress', account_id: `${strategies[0].strategy_id}:STRESS`, profile: 'STRESS' as const, symbol: 'ETHUSDT' }
  render(<PositionList positions={[basePosition, stressPosition]} strategies={strategies} />)
  expect(screen.getByRole('button', { name: '기본 비용' })).toHaveAttribute('aria-pressed', 'true')
  expect(document.querySelector('tbody')?.textContent).toContain('BTCUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('ETHUSDT')
  fireEvent.click(screen.getByRole('button', { name: '보수 비용' }))
  expect(document.querySelector('tbody')?.textContent).toContain('ETHUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('BTCUSDT')
})

test('shows the active runner trail in beginner Korean without hiding the stop', () => {
  const trailingPosition: LeaguePosition = {
    ...basePosition,
    trailing: {
      enabled: true,
      state: 'RUNNER_ACTIVE',
      policy_id: 'EDGE_ADAPTIVE_V1',
      model: 'EDGE_ADAPTIVE',
      activation_price: '101',
      activation_ts_ms: 2_000,
      current_trail: '100.8',
      runner_quantity: '0.6',
      giveback_usdt: '0.2',
      data_health: 'HEALTHY',
      adverse_active: true,
      adverse_reasons: ['OFI_ADVERSE', 'MICROPRICE_ADVERSE'],
    },
  }

  render(<PositionList positions={[trailingPosition]} strategies={strategies} />)

  expect(screen.getByText(/남은 수량 추적 중.*보호선 100.8.*추세 약화 지속 확인/)).toBeInTheDocument()
})

test('uses current-version report costs and drawdown in stored performance statistics', () => {
  const data = dashboardFixture()
  const first = data.strategies[0]
  first.performance.BASE = {
    ...first.performance.BASE,
    fees: '12.34',
    slippage: '23.45',
    maximum_drawdown: '34.56',
    excluded_prior_version_samples: 7,
    trail_activation_count: 2,
    trail_activation_rate: '0.5',
    tp1_fill_rate: '0.5',
    runner_count: 1,
    runner_rate: '0.25',
    runner_net_contribution_usdt: '0.67',
    mfe_capture_ratio_mean: '0.4',
    average_peak_giveback_usdt: '0.12',
    median_peak_giveback_usdt: '0.1',
    p90_peak_giveback_usdt: '0.2',
    trailing_exit_count: 1,
    stop_before_trail_activation_count: 0,
    activation_after_net_negative_exit_count: 0,
    trail_trigger_slippage_usdt: '0.03',
  }
  const firstAccount = data.league_accounts.find((account) => account.strategy_id === first.strategy_id && account.profile === 'BASE')
  if (!firstAccount) throw new Error('BASE fixture account missing')
  firstAccount.fees_usdt = '91.11'
  firstAccount.slippage_usdt = '92.22'
  firstAccount.maximum_drawdown_usdt = '93.33'

  render(<StrategyPerformancePanel data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)

  expect(screen.getByText(/자산은 이번 실행/)).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '이번 실행' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '완료·승률' })).toBeInTheDocument()
  expect(screen.queryByText('계좌 자산 합계')).not.toBeInTheDocument()
  const storedStatistics = document.querySelector('.strategy-performance-panel')?.textContent ?? ''
  expect(storedStatistics).toContain('12.34 USDT')
  expect(storedStatistics).toContain('23.45 USDT')
  expect(storedStatistics).toContain('34.56 USDT')
  expect(storedStatistics).toContain('추적 활성 2건')
  expect(storedStatistics).toContain('남은 수량 관리 1건')
  expect(storedStatistics).toContain('1차 목표 체결 50%')
  expect(storedStatistics).toContain('남은 수량 순기여 +0.67 USDT')
  expect(storedStatistics).toContain('되돌림 중앙 0.1 USDT / 상위 10% 0.2 USDT')
  expect(storedStatistics).toContain('추적 종료 비용 0.03 USDT')
  expect(storedStatistics).toContain('과거 버전 7건 제외')
  expect(storedStatistics).not.toContain('91.11 USDT')
  expect(storedStatistics).not.toContain('92.22 USDT')
  expect(storedStatistics).not.toContain('93.33 USDT')
})

test('hides strategy statistics while the versioned history cache is loading', () => {
  const data = dashboardFixture()
  data.system.dashboard_trade_cache_ready = false

  const { unmount } = render(<StrategyPerformancePanel data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)
  expect(screen.getByRole('status')).toHaveTextContent('준비가 끝나기 전에는 승률·기대값·순위를 표시하지 않습니다.')
  expect(document.querySelector('.strategy-performance-panel')?.textContent).toContain('불러오는 중')
  unmount()

  render(<StrategiesPage strategies={data.strategies} leagueAccounts={data.league_accounts} analyticsReady={false} controlsEnabled onConfigure={vi.fn(async () => undefined)} />)
  expect(screen.getByRole('status')).toHaveTextContent('준비 전 숫자는 순위나 승률로 사용하지 않습니다.')
  expect(document.querySelector('.strategy-compact-table')?.textContent).toContain('불러오는 중')
})

test('derives strategy and account totals from the backend registry payload', () => {
  const data = dashboardFixture()
  const template = data.strategies[0]
  const extraId = 'SYNTHETIC_REGISTRY_EXTENSION_V1'
  const extraStrategy = {
    ...template,
    strategy_id: extraId,
    short_name: '확장 확인',
    display_name_ko: '동적 Registry 확장 확인',
    governance: { ...template.governance, strategy_id: extraId },
    performance: {
      BASE: { ...template.performance.BASE, strategy_id: extraId },
      STRESS: { ...template.performance.STRESS, strategy_id: extraId },
    },
  }
  const extraAccounts = (['BASE', 'STRESS'] as const).map((profile) => ({
    ...data.league_accounts.find((account) => account.profile === profile)!,
    account_id: `${extraId}:${profile}`,
    strategy_id: extraId,
    profile,
  }))
  data.strategies = [...data.strategies, extraStrategy]
  data.league_accounts = [...data.league_accounts, ...extraAccounts]

  render(<StrategyPerformancePanel data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={[]} />)

  expect(screen.queryByText(/개 전략을 같은 공개시장 데이터와 비용 기준으로 비교합니다/)).not.toBeInTheDocument()
  expect(screen.getByText(`총 ${data.league_accounts.length}계좌`)).toBeInTheDocument()
  expect(screen.getAllByText('확장 확인')).toHaveLength(2)
})

test('shows current strategy version scope and excluded prior samples for symbol analytics', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    generated_ts_ms: 1,
    rows: [],
    ranking_rule: '표본 30건',
    analysis_scope: 'CURRENT_STRATEGY_VERSION',
    strategy_version: 'fixture-current',
    excluded_prior_version_samples: 19,
    real_orders_enabled: false,
    auth_required: false,
  }), { status: 200, headers: { 'content-type': 'application/json' } })))

  render(<StrategySymbolPanel strategies={strategies} />)

  await waitFor(() => expect(screen.getByText(/과거 버전 19건 보관/)).toBeInTheDocument())
  expect(screen.getByText(/현재 전략 버전의 독립 공개시장 모의거래만/)).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '어떤 전략이 어떤 종목에 맞았나요?' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '기본 비용' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '보수 비용' })).toBeInTheDocument()
})
