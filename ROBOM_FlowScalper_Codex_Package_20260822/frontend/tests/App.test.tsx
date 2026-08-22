// 대시보드가 PAPER 안전 문구를 영구 표시하는지 검증한다.
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../src/App'

afterEach(() => vi.restoreAllMocks())

test('renders permanent paper-only status', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  render(<App />)
  expect(screen.getByText('PAPER')).toBeInTheDocument()
  expect(screen.getByText('실제 주문 없음')).toBeInTheDocument()
  expect(screen.getByText('OFFLINE FIXTURE')).toBeInTheDocument()
  expect(screen.queryByText('LIVE DATA')).not.toBeInTheDocument()
})

