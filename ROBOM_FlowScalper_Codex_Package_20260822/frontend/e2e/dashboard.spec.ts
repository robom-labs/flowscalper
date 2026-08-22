// 일곱 PAPER 화면의 실제 API 동선·반응형·접근성·콘솔 무오류를 브라우저에서 검증한다.
import { expect, test } from '@playwright/test'
import path from 'node:path'

test('PAPER 안전 상태와 실제 백엔드 워크플로가 반응형으로 작동한다', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  const captureEvidence = process.env.ROBOM_E2E_CAPTURE !== '0'
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(error.message))
  const captureDesktop = async (name: string) => {
    if (!captureEvidence || testInfo.project.name !== 'desktop') return
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: path.resolve('..', 'evidence', 'screenshots', `wave06-${name}-desktop.png`) })
  }

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'FlowScalper' })).toBeVisible()
  await expect(page.getByText('PAPER', { exact: true })).toBeVisible()
  await expect(page.getByText('실제 주문 없음', { exact: true })).toBeVisible()
  await expect(page.getByText('OFFLINE DEMO · LIVE 아님', { exact: true })).toBeVisible()
  await expect(page.locator('.chart-wrap')).toHaveAttribute('aria-label', /실제 캔들·호가 차트/)
  await expect(page.locator('input[type="password"]')).toHaveCount(0)
  await expect(page.getByText('진입 근거 유지 · 120초 강제종료 없음')).toBeVisible()

  const pause = page.getByRole('button', { name: '페이퍼 진입 일시정지' })
  await pause.click()
  await expect(page.getByRole('button', { name: '페이퍼 진입 재개' })).toBeVisible()
  await page.getByRole('button', { name: '페이퍼 진입 재개' }).click()
  await expect(pause).toBeVisible()

  await page.getByLabel('시간구간').selectOption('5')
  await expect(page.locator('.chart-panel h2')).toContainText('5s')
  const scannerSymbol = page.locator('.symbol-button').first()
  await scannerSymbol.click()
  await expect(scannerSymbol).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: '전략', exact: true }).click()
  await expect(page.getByRole('heading', { name: '전략 관리' })).toBeVisible()
  await expect(page.locator('.strategy-card')).toHaveCount(4)
  const firstStrategy = page.locator('.strategy-card').first()
  await firstStrategy.getByRole('button', { name: '가상 관찰' }).click()
  await expect(firstStrategy.locator('.strategy-state')).toHaveText('가상 관찰')
  const longToggle = firstStrategy.getByRole('button', { name: /^LONG/ })
  const previousLongLabel = await longToggle.textContent()
  await longToggle.click()
  await expect(firstStrategy.getByRole('button', { name: previousLongLabel === 'LONG 허용' ? 'LONG 차단' : 'LONG 허용' })).toBeVisible()
  await captureDesktop('strategies')

  await page.getByRole('button', { name: '거래내역', exact: true }).click()
  await expect(page.getByRole('heading', { name: '거래내역' })).toBeVisible()
  await page.getByRole('button', { name: '상세' }).first().click()
  await expect(page.getByRole('heading', { name: '거래 상세' })).toBeVisible()
  await captureDesktop('history')
  await page.getByRole('button', { name: '거래 상세 닫기' }).click()

  await page.getByRole('button', { name: '리플레이', exact: true }).click()
  await expect(page.getByRole('heading', { name: '결정적 리플레이' })).toBeVisible()
  const replayButton = page.getByRole('button', { name: '백엔드 리플레이 실행' })
  await expect(replayButton).toBeEnabled()
  await replayButton.click()
  await expect(page.getByText(/검증 완료 · replay-/)).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.checksum')).toHaveText(/^[a-f0-9]{64}$/)
  await page.getByRole('button', { name: '다음 이벤트' }).click()
  await expect(page.getByText('2 / 3', { exact: true })).toBeVisible()
  await captureDesktop('replay')

  for (const [button, heading, screenshot] of [
    ['성과분석', '성과분석', 'performance'],
    ['위험관리', '위험관리', 'risk'],
    ['시스템', '시스템', 'system'],
  ]) {
    await page.getByRole('button', { name: button, exact: true }).click()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expect(page.getByText('PAPER', { exact: true })).toBeVisible()
    await captureDesktop(screenshot)
  }
  await page.getByText('고급 진단 보기').click()
  await expect(page.getByText('로컬 API 주소')).toBeVisible()
  await expect(page.getByText('프로세스 CPU %')).toBeVisible()
  const memoryValue = page.locator('.diagnostic-grid > div').filter({ hasText: '프로세스 메모리 MB' }).locator('b')
  await expect(memoryValue).not.toHaveText('0')
  await captureDesktop('system-diagnostics')

  await page.getByRole('button', { name: '전략', exact: true }).click()
  const resetStrategy = page.locator('.strategy-card').first()
  await resetStrategy.getByRole('button', { name: '실전 PAPER' }).click()
  const resetLong = resetStrategy.getByRole('button', { name: /^LONG/ })
  if ((await resetLong.textContent()) === 'LONG 차단') await resetLong.click()
  await page.getByRole('button', { name: '라이브', exact: true }).click()
  await page.getByLabel('시간구간').selectOption('1')
  await expect(page.locator('.chart-panel h2')).toContainText('1s')
  const layoutFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
  expect(layoutFits).toBe(true)
  const targetsAreLargeEnough = await page
    .locator('button:visible, select:visible')
    .evaluateAll((targets) => targets.every((target) => target.getBoundingClientRect().height >= 48))
  expect(targetsAreLargeEnough).toBe(true)

  if (captureEvidence) {
    const screenshotPath = path.resolve(
      '..',
      'evidence',
      'screenshots',
      `wave06-dashboard-${testInfo.project.name}.png`,
    )
    await page.screenshot({ path: screenshotPath, fullPage: testInfo.project.name !== 'desktop' })
  }
  expect(consoleErrors).toEqual([])
})
