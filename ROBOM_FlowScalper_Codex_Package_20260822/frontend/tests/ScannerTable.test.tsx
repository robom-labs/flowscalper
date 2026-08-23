// 단순 종목 목록이 방향·진입 상태와 펼침 상세를 정확히 보여주는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { ScannerTable } from '../src/components/ScannerTable'
import type { ScannerRow } from '../src/types'

afterEach(cleanup)

const rows: ScannerRow[] = [
  {
    rank: 2,
    symbol: 'ETHUSDT',
    depth: 'DEEP',
    regime: 'TREND_DOWN',
    strategy: 'CBR_TREND_V1',
    side: 'SHORT',
    score: null,
    net_rr: 1.42,
    expected_cost_bps: 2.1,
    spread_bps: 0.8,
    data_health: 'HEALTHY',
    status: 'OBSERVING',
    reason: '조건을 확인하는 중',
    reason_codes: [],
    calibration: 'CALIBRATING',
  },
  {
    rank: 1,
    symbol: 'BTCUSDT',
    depth: 'DEEP',
    regime: 'RANGE',
    strategy: 'LSA_REVERSAL_V1',
    side: 'LONG',
    score: null,
    net_rr: 1.58,
    expected_cost_bps: 1.9,
    spread_bps: 0.7,
    data_health: 'HEALTHY',
    status: 'QUALIFIED',
    reason: '계획 확정',
    reason_codes: [],
    calibration: 'CALIBRATING',
  },
]

test('shows stable easy direction and entry labels', () => {
  const onSelect = vi.fn()
  render(<ScannerTable rows={rows} venue="BINANCE_FUTURES" selectedSymbol="BTCUSDT" onSelect={onSelect} />)

  expect(screen.getByText('상승 관찰')).toBeInTheDocument()
  expect(screen.getByText('하락 관찰')).toBeInTheDocument()
  expect(screen.getByText('진입 준비')).toBeInTheDocument()
  expect(screen.getByText('확인 중')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /BTCUSDT.*횡보/ }))
  expect(onSelect).toHaveBeenCalledWith('BTCUSDT')
})

test('keeps technical fields behind the detail control', () => {
  render(<ScannerTable rows={rows} venue="BINANCE_FUTURES" selectedSymbol="BTCUSDT" onSelect={vi.fn()} />)
  expect(screen.queryByText('비용 후 손익비')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'BTCUSDT 상세 정보 보기' }))
  expect(screen.getByText('비용 후 손익비')).toBeInTheDocument()
  expect(screen.getByText('1.58')).toBeInTheDocument()
  expect(document.querySelectorAll('.scanner-item')).toHaveLength(2)
  expect(document.querySelector('.scanner-item .drawer-detail-list')).toBeNull()
})

test('keeps the locked order and appends a newly discovered symbol at the bottom', () => {
  vi.useFakeTimers()
  const { container, rerender } = render(<ScannerTable rows={rows} venue="BINANCE_FUTURES" selectedSymbol="BTCUSDT" onSelect={vi.fn()} />)
  const order = () => [...container.querySelectorAll('.scanner-item')].map((item) => item.getAttribute('data-row-key'))
  expect(order()).toEqual(['BINANCE_FUTURES:BTCUSDT', 'BINANCE_FUTURES:ETHUSDT'])

  const newRow = { ...rows[0], rank: 1, symbol: 'XRPUSDT' }
  rerender(<ScannerTable rows={[{ ...rows[0], rank: 1 }, { ...rows[1], rank: 3 }, newRow]} venue="BINANCE_FUTURES" selectedSymbol="BTCUSDT" onSelect={vi.fn()} />)
  act(() => vi.runOnlyPendingTimers())
  expect(order()).toEqual(['BINANCE_FUTURES:BTCUSDT', 'BINANCE_FUTURES:ETHUSDT', 'BINANCE_FUTURES:XRPUSDT'])
  vi.useRealTimers()
})
