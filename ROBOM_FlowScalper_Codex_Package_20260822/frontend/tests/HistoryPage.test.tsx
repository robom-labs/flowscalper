// 현재 Run에서 사라진 거래의 상세 패널이 화면에 남지 않는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { HistoryPage } from '../src/pages/HistoryPage'
import type { HistoryRow } from '../src/types'

afterEach(cleanup)

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
  holding_seconds: 1,
  profile: 'BASE',
  sample_type: 'LIVE_PUBLIC',
}

test('clears stale trade detail when the current history no longer contains it', async () => {
  const view = render(<HistoryPage rows={[trade]} onReplay={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '상세' }))
  expect(screen.getByRole('complementary', { name: '거래 상세' })).toBeInTheDocument()

  view.rerender(<HistoryPage rows={[]} onReplay={vi.fn()} />)

  await waitFor(() => {
    expect(screen.queryByRole('complementary', { name: '거래 상세' })).not.toBeInTheDocument()
  })
})
