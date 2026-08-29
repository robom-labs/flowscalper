// 초보자 요약이 통합계좌와 전략 연구거래를 혼동하지 않도록 검증한다.
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { LivePage } from '../src/pages/LivePage'
import { dashboardFixture } from './fixtures'

test('shows running decisions, qualified signals, and current-version research trades separately', () => {
  const data = dashboardFixture()
  data.status.trade_count = 1
  data.system.strategy_evaluation_count = 648_228
  data.system.qualified_signal_count = 0
  data.operation_status.state = 'RUNNING'
  data.operation_status.title_ko = '작동 중'
  data.operation_status.paper_entry_active = true
  data.strategies[0].performance.BASE.sample_size = 17
  data.strategies[0].performance.STRESS.sample_size = 16
  const onNavigate = vi.fn()

  render(<LivePage data={data} onNavigate={onNavigate} />)

  expect(screen.getByText('통합계좌 완료')).toBeInTheDocument()
  expect(screen.getByText('시장 판정').nextSibling).toHaveTextContent('648,228회')
  expect(screen.getByText('진입조건 통과').nextSibling).toHaveTextContent('0건')
  expect(screen.getByText('현재 전략 연구거래').nextSibling).toHaveTextContent('33건')
  expect(screen.getByText('조건을 낮추지 않고 다음 진입을 기다리는 중입니다.')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '거래 기록 보기' }))
  expect(onNavigate).toHaveBeenCalledWith('history')
})
