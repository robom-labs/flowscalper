// 현재 Run에서 사라진 거래의 상세 패널이 화면에 남지 않는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { HistoryPage } from '../src/pages/HistoryPage'
import type { HistoryRow } from '../src/types'

afterEach(() => {
  cleanup()
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
      version_scope: 'CURRENT', sample_type: 'ALL', strategy_version: 'current-v2',
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
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })
  expect(screen.getByText('1.7초')).toBeInTheDocument()
  expect(screen.getByText('2차 익절')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '상세' }))
  expect(screen.getByRole('complementary', { name: '거래 상세' })).toBeInTheDocument()

  view.rerender(<HistoryPage rows={[]} currentRunId="run-history" onReplay={vi.fn()} />)

  await waitFor(() => {
    expect(screen.queryByRole('complementary', { name: '거래 상세' })).not.toBeInTheDocument()
  })
})

test('shows only the current Run by default and can reveal immutable history', () => {
  const past = { ...trade, run_id: 'run-past', trade_id: 'paper-history-past' }
  render(<HistoryPage rows={[trade, past]} currentRunId="run-history" onReplay={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('계좌 범위'), { target: { value: 'MAIN' } })

  expect(screen.getByText('paper-history-1')).toBeInTheDocument()
  expect(screen.queryByText('paper-history-past')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Run 범위'), { target: { value: 'ALL' } })
  expect(screen.getByText('paper-history-past')).toBeInTheDocument()
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

  expect(screen.getByText(/과거 버전 4건은 원장에 보관/)).toBeInTheDocument()
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

  expect(await screen.findByText('shadow-history-1')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '재생 자료 없음' })).toBeDisabled()
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('account_scope=LEAGUE'),
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
  expect(await screen.findByText('shadow-history-default')).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('표시 2건 · 공동계좌 1건 · 전략별 계좌 1건')
})
