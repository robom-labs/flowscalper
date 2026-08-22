// 주요 PAPER 연구 동선과 영구 안전 표시를 실제 브라우저에서 검증한다.
import { expect, test } from '@playwright/test'
import path from 'node:path'

test('PAPER 안전 상태와 전체 워크플로가 반응형으로 작동한다', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(error.message))

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'FlowScalper' })).toBeVisible()
  await expect(page.getByText('PAPER', { exact: true })).toBeVisible()
  await expect(page.getByText('실제 주문 없음', { exact: true })).toBeVisible()
  await expect(page.getByText('OFFLINE FIXTURE · LIVE 아님')).toBeVisible()
  await expect(page.getByLabel('SOLUSDT 차트, 진입 TP SL 선 포함')).toBeVisible()
  await expect(page.getByText('비용 비중 34% > 허용 30%').first()).toBeVisible()
  await expect(page.locator('input[type="password"]')).toHaveCount(0)
  await expect(page.getByText('진입 근거 유지 · 120초 강제종료 없음')).toBeVisible()

  const pause = page.getByRole('button', { name: '페이퍼 진입 일시정지' })
  await pause.click()
  await expect(page.getByRole('button', { name: '페이퍼 진입 재개' })).toBeVisible()
  await page.getByRole('button', { name: '페이퍼 진입 재개' }).click()
  await expect(pause).toBeVisible()

  for (const [button, heading] of [
    ['거래내역', '거래내역'],
    ['리플레이', '결정적 리플레이'],
    ['성과분석', '성과분석'],
    ['위험관리', '위험관리'],
    ['시스템', '시스템'],
  ]) {
    await page.getByRole('button', { name: button, exact: true }).click()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expect(page.getByText('PAPER', { exact: true })).toBeVisible()
  }

  await page.getByRole('button', { name: '라이브', exact: true }).click()
  const layoutFits = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )
  expect(layoutFits).toBe(true)
  const targetsAreLargeEnough = await page
    .locator('.navigation button')
    .evaluateAll((buttons) => buttons.every((button) => button.getBoundingClientRect().height >= 48))
  expect(targetsAreLargeEnough).toBe(true)

  const screenshotPath = path.resolve(
    '..',
    'artifacts',
    'screenshots',
    `dashboard-${testInfo.project.name}.png`,
  )
  await page.screenshot({ path: screenshotPath, fullPage: true })
  expect(consoleErrors).toEqual([])
})
