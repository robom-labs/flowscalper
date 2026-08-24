// 8전략·16독립계좌가 쉬운 전략 설정과 진행 거래에서 분리 표시되는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LeaguePositionsPage } from '../src/pages/LeaguePositionsPage'
import { StrategiesPage } from '../src/pages/StrategiesPage'
import type { LeaguePosition } from '../src/types'
import { leagueAccounts, strategies } from './fixtures'

afterEach(cleanup)

test('shows eight compact strategy rows, easy modes and BASE/STRESS account detail', () => {
  render(<StrategiesPage strategies={strategies} leagueAccounts={leagueAccounts} onConfigure={vi.fn(async () => undefined)} />)
  expect(document.querySelectorAll('.strategy-compact-table tbody tr')).toHaveLength(8)
  expect(document.querySelectorAll('.strategy-inline-modes button[aria-pressed="true"]')).toHaveLength(8)
  expect(screen.queryByText('기록만 하기')).not.toBeInTheDocument()
  expect(screen.getByText('8/8 전략 켜짐 · 실제 주문 0')).toBeInTheDocument()
  expect([...document.querySelectorAll('.strategy-inline-modes button[aria-pressed="true"]')].every((button) => button.textContent?.includes('모의 중'))).toBe(true)

  fireEvent.click(screen.getAllByRole('button', { name: '자세히' })[0])
  expect(screen.getByRole('dialog', { name: '전략 상세 정보' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'BASE 가상계좌' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'STRESS 가상계좌' })).toBeInTheDocument()
  expect(screen.getAllByText(/이번 Run 현재자산/).length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText(/저장된 전체 독립 PAPER 거래 기준/).length).toBeGreaterThanOrEqual(2)
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

test('uses BASE as the default open-trade filter and can reveal STRESS', () => {
  const stressPosition = { ...basePosition, trade_id: 'trade-stress', candidate_id: 'candidate-stress', account_id: `${strategies[0].strategy_id}:STRESS`, profile: 'STRESS' as const, symbol: 'ETHUSDT' }
  render(<LeaguePositionsPage positions={[basePosition, stressPosition]} strategies={strategies} />)
  expect(screen.getByRole('button', { name: 'BASE' })).toHaveAttribute('aria-pressed', 'true')
  expect(document.querySelector('tbody')?.textContent).toContain('BTCUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('ETHUSDT')
  fireEvent.click(screen.getByRole('button', { name: 'STRESS' }))
  expect(document.querySelector('tbody')?.textContent).toContain('ETHUSDT')
  expect(document.querySelector('tbody')?.textContent).not.toContain('BTCUSDT')
})
