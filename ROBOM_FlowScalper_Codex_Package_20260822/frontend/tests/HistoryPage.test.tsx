// 현재 Run에서 사라진 거래의 상세 패널이 화면에 남지 않는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
}

function historyResponse(rows: HistoryRow[]) {
  return new Response(JSON.stringify({
    rows,
    scope: {
      run_scope: 'CURRENT', account_scope: 'ALL', profile: 'ALL',
      version_scope: 'ALL', sample_type: 'ALL', strategy_version: 'current-v2',
      returned_count: rows.length, limit: 1000,
    },
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
  fireEvent.click(screen.getByRole('button', { name: '자세히' }))
  expect(screen.getByRole('complementary', { name: 'BTCUSDT 거래 결과' })).toBeInTheDocument()
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

  view.rerender(<HistoryPage rows={[]} currentRunId="run-history" onReplay={vi.fn()} />)

  await waitFor(() => {
    expect(screen.queryByRole('complementary', { name: 'BTCUSDT 거래 결과' })).not.toBeInTheDocument()
  })
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

test('shows only the current Run by default and can reveal immutable history', () => {
  const past = { ...trade, run_id: 'run-past', trade_id: 'paper-history-past' }
  render(<HistoryPage rows={[trade, past]} currentRunId="run-history" onReplay={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('전략 버전'), { target: { value: 'CURRENT' } })
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })

  expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(1)
  expect(screen.queryByText('paper-history-1')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Run 범위'), { target: { value: 'ALL' } })
  expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(2)
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
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({
    rows: [leagueTrade],
    scope: {
      run_scope: 'CURRENT', account_scope: 'LEAGUE', profile: 'ALL',
      version_scope: 'CURRENT', sample_type: 'ALL', strategy_version: 'current-v2',
      returned_count: 1, limit: 1000,
    },
    paper_only: true, real_orders_enabled: false, auth_required: false,
  }), { status: 200, headers: { 'content-type': 'application/json' } }))
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
  await waitFor(() => expect(document.querySelectorAll('.history-table tbody tr')).toHaveLength(2))
  expect(screen.getByRole('status')).toHaveTextContent('현재 조건 2건 · 공동 1건 · 전략별 1건')
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

test('does not describe prior EDGE_DECAY records as if they used the current cost gate', () => {
  const priorTrade = { ...trade, exit_reason: 'EDGE_DECAY', strategy_version: 'prior-v1' }
  render(
    <HistoryPage
      rows={[priorTrade]}
      currentRunId="run-history"
      historyScope={{ strategy_version: 'current-v2', excluded_prior_version_samples: 1 }}
      onReplay={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })

  expect(document.querySelector('.history-table')).toHaveTextContent('진입 근거 약화(과거 기준)')
  expect(document.querySelector('.history-table')).toHaveTextContent('현재 버전은 비용 이상의 가격 악화도 함께 확인합니다.')
  expect(document.querySelector('.history-table')).not.toHaveTextContent('가격이 왕복 비용 구간보다 불리하게 움직이고')
})
