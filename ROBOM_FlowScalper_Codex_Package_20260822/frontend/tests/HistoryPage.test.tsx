// 현재 Run에서 사라진 거래의 상세 패널이 화면에 남지 않는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { HistoryPage } from '../src/pages/HistoryPage'
import type { HistoryRow } from '../src/types'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const trade: HistoryRow = {
  run_id: 'run-history',
  trade_id: 'paper-history-1',
  opportunity_id: 'opportunity-history-1',
  symbol: 'BTCUSDT',
  strategy: 'CBR_CONTINUATION_V1',
  side: 'LONG',
  entry: '100',
  exit: '101',
  entry_ts_ms: 1_000,
  exit_ts_ms: 2_000,
  initial_stop: '99',
  take_profit: '103',
  take_profit_1: '102',
  take_profit_2: '103',
  time_to_tp1_ms: 800,
  time_to_tp2_ms: 1_696,
  time_to_stop_ms: null,
  trailing_activation_ts_ms: 1_500,
  runner_started_ts_ms: 1_700,
  peak_unrealized_usdt: '1.2',
  giveback_usdt: '0.35',
  runner_net_pnl_usdt: '0.4',
  trail_trigger_slippage_usdt: '0.02',
  trailing_state_checksum: 'a'.repeat(64),
  quantity: '1',
  exit_reason: 'TP2',
  gross_pnl: '1',
  fees: '0.1',
  slippage: '0.05',
  net_pnl: '0.85',
  holding_ms: 1_696,
  holding_seconds: 1,
  profile: 'BASE',
  sample_type: 'LIVE_PUBLIC',
  strategy_version: 'current-v2',
}

function historyResponse(rows: HistoryRow[]) {
  const opportunities = new Map<string, HistoryRow[]>()
  for (const row of rows) {
    const opportunityId = row.opportunity_id ?? row.candidate_id ?? row.signal_event_id
    if (!opportunityId) continue
    const key = [row.run_id, row.strategy, row.strategy_version, opportunityId, row.symbol, row.side].join(':')
    opportunities.set(key, [...(opportunities.get(key) ?? []), row])
  }
  const grouped = [...opportunities.values()].map((opportunityRows) => {
    const first = opportunityRows[0]
    const accountGroups = new Map<string, HistoryRow[]>()
    for (const row of opportunityRows) {
      const scope = row.account_scope ?? 'MAIN'
      const accountGroupId = scope === 'LEAGUE' ? row.strategy : row.account_id ?? 'SHARED_PAPER'
      const key = `${scope}:${accountGroupId}`
      accountGroups.set(key, [...(accountGroups.get(key) ?? []), row])
    }
    const mappedGroups = [...accountGroups.values()].map((accountRows) => {
      const accountFirst = accountRows[0]
      const accountScope = accountFirst.account_scope ?? 'MAIN'
      const accountGroupId = accountScope === 'LEAGUE' ? accountFirst.strategy : accountFirst.account_id ?? 'SHARED_PAPER'
      const profiles = Object.fromEntries(['BASE', 'STRESS'].flatMap((profile) => {
        const profileRows = accountRows.filter((row) => row.profile === profile)
        return profileRows.length ? [[profile, profileRows]] : []
      }))
      return {
        account_scope: accountScope,
        account_group_id: accountGroupId,
        account_ids: [...new Set(accountRows.map((row) => row.account_id ?? accountGroupId))],
        profiles,
        profile_account_refs: Object.fromEntries(accountRows.map((row) => [row.profile, {
          account_scope: row.account_scope ?? 'MAIN', account_id: row.account_id ?? accountGroupId,
        }])),
        rows: accountRows,
        raw_result_row_count: accountRows.length,
        base_result_row_count: accountRows.filter((row) => row.profile === 'BASE').length,
        stress_result_row_count: accountRows.filter((row) => row.profile === 'STRESS').length,
        partial_exit_row_count: Math.max(0, accountRows.length - new Set(accountRows.map((row) => row.profile)).size),
      }
    })
    return {
      key: {
        run_id: first.run_id, strategy_id: first.strategy,
        strategy_version: first.strategy_version ?? 'UNKNOWN',
        opportunity_id: first.opportunity_id ?? first.candidate_id ?? first.signal_event_id,
        symbol: first.symbol, side: first.side,
      },
      family_id: null, family_label_ko: null, variant_label_ko: null,
      entry_ts_ms: Math.min(...opportunityRows.map((row) => row.entry_ts_ms)),
      exit_ts_ms: Math.max(...opportunityRows.map((row) => row.exit_ts_ms)),
      profiles: {}, account_groups: mappedGroups, rows: opportunityRows,
      raw_result_row_count: opportunityRows.length,
      base_result_row_count: opportunityRows.filter((row) => row.profile === 'BASE').length,
      stress_result_row_count: opportunityRows.filter((row) => row.profile === 'STRESS').length,
      partial_exit_row_count: Math.max(0, opportunityRows.length - new Set(opportunityRows.map((row) => row.profile)).size),
      replay_available: opportunityRows.some((row) => row.replay_available),
    }
  })
  return new Response(JSON.stringify({
    schema_version: 1,
    opportunities: grouped,
    counts: {
      unique_opportunities: grouped.length,
      raw_result_rows: rows.length,
      base_result_rows: rows.filter((row) => row.profile === 'BASE').length,
      stress_result_rows: rows.filter((row) => row.profile === 'STRESS').length,
      unresolved_result_rows: rows.length - grouped.reduce((total, opportunity) => total + opportunity.rows.length, 0),
    },
    grouping_status: 'PROVEN',
    source_status: 'COMPLETE',
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }), { status: 200, headers: { 'content-type': 'application/json' } })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([trade])))
})

test('clears stale trade detail when the current history no longer contains it', async () => {
  const view = render(<HistoryPage rows={[trade]} currentRunId="run-history" onReplay={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('전략 버전'), { target: { value: 'CURRENT' } })
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })
  expect(screen.getByText('1.7초')).toBeInTheDocument()
  expect(screen.getByText('2차 익절')).toBeInTheDocument()
  const openButton = screen.getByRole('button', { name: '자세히' })
  openButton.focus()
  fireEvent.click(openButton)
  expect(screen.getByRole('dialog', { name: '거래 상세' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '거래 상세 닫기' })).toHaveFocus()
  expect(screen.getByText('1차 목표까지')).toBeInTheDocument()
  expect(screen.getByText('2차 목표까지')).toBeInTheDocument()
  expect(screen.getByText('1차 목표')).toBeInTheDocument()
  expect(screen.getByText('102')).toBeInTheDocument()
  expect(screen.getByText('2차 목표')).toBeInTheDocument()
  expect(screen.getByText('103')).toBeInTheDocument()
  expect(screen.getByText('손절까지')).toBeInTheDocument()
  expect(screen.getByText('해당 없음')).toBeInTheDocument()
  expect(screen.getByText('추적 익절 자세히')).toBeInTheDocument()
  expect(screen.getByText('남은 수량 추적')).toBeInTheDocument()
  expect(screen.getByText('0.7초 뒤')).toBeInTheDocument()
  expect(screen.getByText('최고 미실현 손익')).toBeInTheDocument()
  expect(screen.getByText('고점 대비 되돌림')).toBeInTheDocument()
  expect(screen.getByText('남은 수량 순기여')).toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: '거래 상세' })).not.toBeInTheDocument()
  expect(openButton).toHaveFocus()
  fireEvent.click(openButton)

  view.rerender(<HistoryPage rows={[]} currentRunId="run-history" onReplay={vi.fn()} />)

  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: '거래 상세' })).not.toBeInTheDocument()
  })
})

test('separates fills, costs, movement, replay, and hidden technical identifiers in completed detail', () => {
  const detailedTrade: HistoryRow = {
    ...trade,
    candidate_id: 'candidate-technical-hidden',
    signal_event_id: 'event-technical-hidden',
    mae_r: '-0.35',
    mfe_r: '1.4',
    config_hash: 'b'.repeat(64),
    replay_available: true,
    fill_evidence_state: 'PRESENT',
    fill_evidence_reason_ko: '원시 PAPER 체결과 거래 비용 합계를 확인했습니다.',
    fills: [
      {
        fill_id: 'fill-entry-hidden',
        order_id: 'order-entry-hidden',
        ts_ms: 1_200,
        side: 'BUY',
        intent: 'ENTRY_IOC',
        price: '100',
        quantity: '1',
        fee_usdt: '0.04',
        slippage_usdt: '0.02',
      },
      {
        fill_id: 'fill-exit-hidden',
        order_id: 'order-exit-hidden',
        ts_ms: 1_900,
        side: 'SELL',
        intent: 'TAKE_PROFIT',
        price: '101',
        quantity: '1',
        fee_usdt: '0.06',
        slippage_usdt: '0.03',
      },
    ],
  }
  const replay = vi.fn()
  render(<HistoryPage rows={[detailedTrade]} currentRunId="run-history" onReplay={replay} />)
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))

  const detail = screen.getByRole('dialog', { name: '거래 상세' })
  const fills = within(detail).getByRole('list', { name: '확인된 PAPER 체결 원장' })
  expect(fills).toHaveTextContent('1. 진입 체결')
  expect(fills).toHaveTextContent('매수')
  expect(fills).toHaveTextContent('2. 목표가 청산')
  expect(fills).toHaveTextContent('매도')
  expect(fills).toHaveTextContent('0.04 USDT')
  expect(fills).toHaveTextContent('0.06 USDT')
  expect(within(detail).queryByText(/원시 fill 미제공/)).not.toBeInTheDocument()
  expect(within(detail).queryByText('fill-entry-hidden')).not.toBeInTheDocument()
  const costSection = within(detail).getByRole('heading', { name: '비용과 종료' }).closest('section')
  if (!(costSection instanceof HTMLElement)) throw new Error('cost section missing')
  expect(within(costSection).getByText('수수료').parentElement).toHaveTextContent('0.1 USDT')
  expect(within(costSection).getByText('슬리피지').parentElement).toHaveTextContent('0.05 USDT')
  expect(within(costSection).getByText('종료 이유').parentElement).toHaveTextContent('2차 익절')
  expect(within(detail).getByText('최대 유리 변동(MFE)').parentElement).toHaveTextContent('1.4R')
  expect(within(detail).getByText('최대 불리 변동(MAE)').parentElement).toHaveTextContent('-0.35R')
  expect(within(detail).getByText('고점 대비 되돌림(giveback)').parentElement).toHaveTextContent('0.35 USDT')
  expect(within(detail).getByRole('button', { name: '선택한 비용 결과 다시보기' })).toBeEnabled()

  expect(within(detail).getByText('candidate-technical-hidden')).not.toBeVisible()
  expect(within(detail).getByText('event-technical-hidden')).not.toBeVisible()
  expect(within(detail).getByText('a'.repeat(64))).not.toBeVisible()
  expect(within(detail).getByText('b'.repeat(64))).not.toBeVisible()
  fireEvent.click(within(detail).getByText('기술 정보'))
  expect(within(detail).getByText('기술 상세')).toBeVisible()
  expect(within(detail).getByText('candidate-technical-hidden')).toBeVisible()
  expect(within(detail).getByText('event-technical-hidden')).toBeVisible()
  expect(within(detail).getByText('a'.repeat(64))).toBeVisible()
  expect(within(detail).getByText('b'.repeat(64))).toBeVisible()

  fireEvent.click(within(detail).getByRole('button', { name: '선택한 비용 결과 다시보기' }))
  expect(replay).toHaveBeenCalledWith(detailedTrade)
})

test('separates shadow and current-main missing fill evidence without inventing fills', () => {
  const shadowTrade: HistoryRow = {
    ...trade,
    trade_id: 'shadow-no-fill',
    account_scope: 'LEAGUE',
    account_id: `${trade.strategy}:BASE`,
    fill_evidence_state: 'SHADOW_UNAVAILABLE',
    fill_evidence_reason_ko: '전략별 가상계좌 기록은 집계 결과만 보존되어 원시 fill이 제공되지 않습니다.',
    fills: [],
  }
  const view = render(
    <HistoryPage
      rows={[shadowTrade]}
      currentRunId="run-history"
      providedScope="CURRENT_ALL"
      onReplay={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))
  expect(screen.getByText('전략별 가상계좌 원시 fill 미제공 · NOT_PROVEN')).toBeInTheDocument()
  expect(screen.getByText(/집계 결과만 보존/)).toBeInTheDocument()
  expect(screen.queryByRole('list', { name: '확인된 PAPER 체결 원장' })).not.toBeInTheDocument()

  view.unmount()
  render(
    <HistoryPage
      rows={[{
        ...trade,
        fill_evidence_state: 'CURRENT_MAIN_NO_FILL',
        fill_evidence_reason_ko: '현재 공동 가상계좌 거래에 연결된 원시 체결이 없습니다.',
        fills: [],
      }]}
      currentRunId="run-history"
      providedScope="CURRENT_ALL"
      onReplay={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))
  expect(screen.getByText('현재 공동 가상계좌 체결 없음 · NOT_PROVEN')).toBeInTheDocument()
})

test('labels unavailable movement metrics as not measured', () => {
  render(<HistoryPage rows={[trade]} currentRunId="run-history" onReplay={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))

  const detail = screen.getByRole('dialog', { name: '거래 상세' })
  expect(within(detail).getByText('최대 유리 변동(MFE)').parentElement).toHaveTextContent('측정 전')
  expect(within(detail).getByText('최대 불리 변동(MAE)').parentElement).toHaveTextContent('측정 전')
})

test('labels a legacy single target without pretending it is TP1 or TP2', () => {
  render(
    <HistoryPage
      rows={[{ ...trade, take_profit_1: null, take_profit_2: null }]}
      currentRunId="run-history"
      onReplay={vi.fn()}
    />,
  )

  fireEvent.change(screen.getByLabelText('전략 버전'), { target: { value: 'CURRENT' } })
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))

  expect(screen.getByText('목표가(과거 기록)')).toBeInTheDocument()
  expect(screen.queryByText('1차 목표')).not.toBeInTheDocument()
  expect(screen.queryByText('2차 목표')).not.toBeInTheDocument()
})

test('shows only the current Run by default and can reveal immutable history', async () => {
  const past = { ...trade, run_id: 'run-past', trade_id: 'paper-history-past' }
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([trade, past])))
  render(<HistoryPage rows={[trade, past]} currentRunId="run-history" onReplay={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('전략 버전'), { target: { value: 'CURRENT' } })
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })

  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))
  expect(screen.queryByText('paper-history-1')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Run 범위'), { target: { value: 'ALL' } })
  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(2))
})

test('explains that prior strategy-version trades stay archived outside the current list', () => {
  render(
    <HistoryPage
      rows={[trade]}
      currentRunId="run-history"
      historyScope={{ strategy_version: 'current-v2', excluded_prior_version_samples: 4 }}
      onReplay={vi.fn()}
    />,
  )

  expect(screen.getByText(/과거 버전 4건은 안전하게 보관/)).toBeInTheDocument()
})

test('loads independent strategy accounts and marks rows without replay events', async () => {
  const leagueTrade: HistoryRow = {
    ...trade,
    trade_id: 'shadow-history-1',
    account_scope: 'LEAGUE',
    account_id: 'CBR_CONTINUATION_V1:STRESS',
    profile: 'STRESS',
    strategy_version: 'current-v2',
    replay_available: false,
  }
  const fetchMock = vi.fn(async () => historyResponse([leagueTrade]))
  vi.stubGlobal('fetch', fetchMock)
  render(<HistoryPage rows={[trade]} currentRunId="run-history" onReplay={vi.fn()} />)

  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'LEAGUE' } })

  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))
  expect(screen.getByRole('button', { name: '다시보기 없음' })).toBeDisabled()
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('account_scope=LEAGUE'),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  )
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('version_scope=CURRENT'),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  )
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('/api/trades?'),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  )
})

test('shows all PAPER accounts by default with a visible loading and count summary', async () => {
  const leagueTrade: HistoryRow = {
    ...trade,
    trade_id: 'shadow-history-default',
    account_scope: 'LEAGUE',
    account_id: 'QUEUE_REACTIVE_V1:BASE',
  }
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([trade, leagueTrade])))

  render(<HistoryPage rows={[trade]} currentRunId="run-history" onReplay={vi.fn()} />)

  expect(screen.getByLabelText('계좌 범위')).toHaveValue('ALL')
  expect(screen.getByLabelText('전략 버전')).toHaveValue('CURRENT')
  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))
  expect(screen.getByRole('status')).toHaveTextContent('진입기회 1건 · 원장 결과 2행 · BASE 2 · STRESS 0 · 공동 1건 · 전략별 1건')
  expect(document.querySelector('.history-table tbody tr')).toHaveTextContent('계좌 결과 비교')
  fireEvent.click(screen.getByRole('button', { name: '결과 비교' }))
  const results = screen.getByRole('group', { name: '진입기회별 세부 결과' })
  expect(results).toHaveTextContent('공동 가상계좌 · 기본 비용')
  expect(results).toHaveTextContent('전략별 가상계좌 · 기본 비용')
  expect(screen.getByText(/중복 진입기회가 아닙니다/)).toBeInTheDocument()
})

test('groups BASE and STRESS ledger rows from the same opportunity without deleting either result', async () => {
  const baseTrade: HistoryRow = {
    ...trade,
    trade_id: 'shadow-opportunity-base',
    candidate_id: 'candidate-same-opportunity',
    signal_event_id: 'signal-same-opportunity',
    opportunity_id: 'candidate-same-opportunity',
    account_scope: 'LEAGUE',
    account_id: 'CBR_CONTINUATION_V1:BASE',
    profile: 'BASE',
    strategy_version: 'current-v2',
    net_pnl: '8.831972',
    fees: '0.9',
    slippage: '0.3',
    replay_available: true,
  }
  const stressTrade: HistoryRow = {
    ...baseTrade,
    trade_id: 'shadow-opportunity-stress',
    account_id: 'CBR_CONTINUATION_V1:STRESS',
    profile: 'STRESS',
    net_pnl: '6.665085',
    fees: '1.8',
    slippage: '0.6',
  }
  const replay = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([stressTrade, baseTrade])))

  render(<HistoryPage rows={[]} currentRunId="run-history" onReplay={replay} />)

  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))
  expect(screen.getByRole('status')).toHaveTextContent('진입기회 1건 · 원장 결과 2행 · BASE 1 · STRESS 1 · 공동 0건 · 전략별 1건')
  const resultRow = document.querySelector('.history-table tbody tr')
  expect(resultRow).toHaveTextContent('기본 비용')
  expect(resultRow).toHaveTextContent('+8.832 USDT')
  expect(resultRow).toHaveTextContent('보수 비용')
  expect(resultRow).toHaveTextContent('+6.665 USDT')
  expect(resultRow).toHaveTextContent('같은 진입기회 · 비용 가정만 다름')

  fireEvent.click(screen.getByRole('button', { name: '비용별 결과' }))
  expect(screen.getByText(/중복 거래가 아닙니다/)).toBeInTheDocument()
  const profileTabs = screen.getByRole('group', { name: '비용별 거래 결과' })
  const stressButton = within(profileTabs).getByRole('button', { name: /보수 비용/ })
  expect(stressButton).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(stressButton)
  expect(stressButton).toHaveAttribute('aria-pressed', 'true')
  expect(document.querySelector('.trade-result-lead')).toHaveTextContent('+6.665 USDT')

  fireEvent.click(screen.getByRole('button', { name: '선택한 비용 결과 다시보기' }))
  expect(replay).toHaveBeenCalledWith(expect.objectContaining(stressTrade))
})

test('collapses same-profile partial exits inside one exact opportunity', async () => {
  const baseTrade: HistoryRow = {
    ...trade,
    trade_id: 'shadow-opportunity-base',
    candidate_id: 'candidate-same-opportunity',
    opportunity_id: 'candidate-same-opportunity',
    account_scope: 'LEAGUE',
    account_id: 'CBR_CONTINUATION_V1:BASE',
    profile: 'BASE',
    strategy_version: 'current-v2',
  }
  const duplicateBaseTrade: HistoryRow = {
    ...baseTrade,
    trade_id: 'shadow-opportunity-base-duplicate',
  }
  const stressTrade: HistoryRow = {
    ...baseTrade,
    trade_id: 'shadow-opportunity-stress',
    account_id: 'CBR_CONTINUATION_V1:STRESS',
    profile: 'STRESS',
  }
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([
    stressTrade,
    baseTrade,
    duplicateBaseTrade,
  ])))

  render(<HistoryPage rows={[]} currentRunId="run-history" onReplay={vi.fn()} />)

  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))
  expect(screen.getByRole('status')).toHaveTextContent('진입기회 1건 · 원장 결과 3행 · BASE 2 · STRESS 1')
  expect(screen.getByText('+1.7 USDT')).toBeInTheDocument()
})

test('does not promote mixed partial-row fill evidence to PRESENT', async () => {
  const presentPartial: HistoryRow = {
    ...trade,
    trade_id: 'partial-present',
    opportunity_id: 'partial-mixed-opportunity',
    account_scope: 'MAIN',
    account_id: 'SHARED_PAPER',
    replay_available: true,
    fill_evidence_state: 'PRESENT',
    fill_evidence_reason_ko: '원시 PAPER 체결과 거래 비용 합계를 확인했습니다.',
    fills: [{
      fill_id: 'partial-fill', order_id: 'partial-order', ts_ms: 1_200,
      side: 'BUY', intent: 'ENTRY_IOC', price: '100', quantity: '1',
      fee_usdt: '0.1', slippage_usdt: '0.05',
    }],
  }
  const unavailablePartial: HistoryRow = {
    ...trade,
    trade_id: 'partial-legacy',
    opportunity_id: 'partial-mixed-opportunity',
    account_scope: 'MAIN',
    account_id: 'SHARED_PAPER',
    replay_available: false,
    fill_evidence_state: 'LEGACY_UNAVAILABLE',
    fill_evidence_reason_ko: '과거 공동 가상계좌 기록에는 원시 체결 연결 정보가 없습니다.',
    fills: [],
  }
  const currentNoFillPartial: HistoryRow = {
    ...trade,
    trade_id: 'partial-current-no-fill',
    opportunity_id: 'partial-mixed-opportunity',
    account_scope: 'MAIN',
    account_id: 'SHARED_PAPER',
    replay_available: false,
    fill_evidence_state: 'CURRENT_MAIN_NO_FILL',
    fill_evidence_reason_ko: '현재 공동 가상계좌 거래에 연결된 원시 체결이 없습니다.',
    fills: [],
  }
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([
    presentPartial,
    currentNoFillPartial,
    unavailablePartial,
  ])))

  render(<HistoryPage rows={[]} currentRunId="run-history" onReplay={vi.fn()} />)

  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))
  expect(screen.getByText('과거 기록 원시 fill 미제공 · NOT_PROVEN')).toBeInTheDocument()
  expect(screen.queryByRole('list', { name: '확인된 PAPER 체결 원장' })).not.toBeInTheDocument()
})

test('shows an open PAPER position and moves the completed trade into refreshed history', async () => {
  const completedTrade: HistoryRow = {
    ...trade,
    trade_id: 'paper-history-2',
    symbol: 'ETHUSDT',
    exit_ts_ms: 3_000,
  }
  let requestCount = 0
  const fetchMock = vi.fn(async () => {
    requestCount += 1
    return historyResponse(requestCount === 1 ? [trade] : [completedTrade, trade])
  })
  vi.stubGlobal('fetch', fetchMock)
  const view = render(
    <HistoryPage
      rows={[trade]}
      currentRunId="run-history"
      openPositionCount={1}
      onReplay={vi.fn()}
    />,
  )

  expect(screen.getByText('현재 진행 중인 모의 포지션 1건')).toBeInTheDocument()
  expect(screen.getByText(/종료되면 자동으로 완료 기록에 추가/)).toBeInTheDocument()
  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1))

  view.rerender(
    <HistoryPage
      rows={[trade]}
      currentRunId="run-history"
      openPositionCount={0}
      onReplay={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '지금 새로고침' }))

  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(2))
  expect(screen.getByText('ETHUSDT')).toBeInTheDocument()
  expect(screen.getByText('현재 진행 중인 모의 포지션 0건')).toBeInTheDocument()
  expect(screen.getByText(/5초마다 자동 확인/)).toBeInTheDocument()
})

test('automatically refreshes completed PAPER history every five seconds', async () => {
  vi.useFakeTimers()
  const completedTrade: HistoryRow = {
    ...trade,
    trade_id: 'paper-history-auto-refresh',
    symbol: 'SOLUSDT',
    exit_ts_ms: 4_000,
  }
  let requestCount = 0
  vi.stubGlobal('fetch', vi.fn(async () => {
    requestCount += 1
    return historyResponse(requestCount === 1 ? [trade] : [completedTrade, trade])
  }))

  render(<HistoryPage rows={[trade]} currentRunId="run-history" onReplay={vi.fn()} />)

  await vi.waitFor(() => {
    expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1)
  })
  await vi.advanceTimersByTimeAsync(5_000)
  await vi.waitFor(() => {
    expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(2)
  })
  expect(screen.getByText('SOLUSDT')).toBeInTheDocument()
})

test('keeps raw ledger identifiers and exit codes out of the normal table', () => {
  const edgeTrade = { ...trade, exit_reason: 'EDGE_DECAY', trade_id: 'paper-secret-technical-id' }
  render(<HistoryPage rows={[edgeTrade]} currentRunId="run-history" onReplay={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })

  const table = document.querySelector('.history-table')
  expect(table).toHaveTextContent('가격·근거 동시 악화')
  expect(table).not.toHaveTextContent('EDGE_DECAY')
  expect(table).not.toHaveTextContent('paper-secret-technical-id')

  fireEvent.click(screen.getByRole('button', { name: '자세히' }))
  expect(screen.getByText('기술 정보')).toBeInTheDocument()
  expect(screen.getByText('paper-secret-technical-id')).not.toBeVisible()
})

test('does not describe prior EDGE_DECAY records as if they used the current cost gate', async () => {
  const priorTrade = { ...trade, exit_reason: 'EDGE_DECAY', strategy_version: 'prior-v1' }
  vi.stubGlobal('fetch', vi.fn(async () => historyResponse([priorTrade])))
  render(
    <HistoryPage
      rows={[priorTrade]}
      currentRunId="run-history"
      historyScope={{ strategy_version: 'current-v2', excluded_prior_version_samples: 1 }}
      onReplay={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })
  fireEvent.change(screen.getByLabelText('전략 버전'), { target: { value: 'ALL' } })

  await waitFor(() => expect(document.querySelector('.history-table')).toHaveTextContent('진입 근거 약화(과거 기준)'))
  expect(document.querySelector('.history-table')).toHaveTextContent('현재 버전은 비용 이상의 가격 악화도 함께 확인합니다.')
  expect(document.querySelector('.history-table')).not.toHaveTextContent('가격이 왕복 비용 구간보다 불리하게 움직이고')
})
