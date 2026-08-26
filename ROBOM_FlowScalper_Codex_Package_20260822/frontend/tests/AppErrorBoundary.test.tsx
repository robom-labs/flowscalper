// 최상위 화면 예외가 빈 화면 대신 PAPER 안전 복구 안내로 전환되는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { AppErrorBoundary } from '../src/components/AppErrorBoundary'

function BrokenWorkspace(): never {
  throw new Error('required_market_data is missing')
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

test('fails closed with a recoverable PAPER-only screen when a child crashes', () => {
  const reload = vi.fn()

  render(
    <AppErrorBoundary reload={reload}>
      <BrokenWorkspace />
    </AppErrorBoundary>,
  )

  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('화면을 표시하는 중 문제가 생겼습니다.')
  expect(alert).toHaveTextContent('PAPER 계산만 사용하며 실제 주문은 계속 0입니다.')
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '화면 다시 불러오기' }))
  expect(reload).toHaveBeenCalledTimes(1)
})
