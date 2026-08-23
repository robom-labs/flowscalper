// 초보자 홈·전략 리그·진행 거래·고급 차트와 비동기 제어를 실제 로컬 API로 검증한다.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const terminalStates = new Set(['COMPLETED', 'FAILED_RETRYABLE', 'FAILED_BLOCKED', 'CANCELLED'])
const screenshots = path.resolve('..', 'evidence', 'screenshots')

async function currentOperation(request: APIRequestContext) {
  const response = await request.get('/api/control/operations/current')
  expect(response.ok()).toBe(true)
  return await response.json() as { operation_id: string; state: string; retryable: boolean }
}

async function waitForOperation(request: APIRequestContext, operationId: string, expected: string) {
  await expect.poll(async () => {
    const response = await request.get(`/api/control/operations/${operationId}`)
    return (await response.json() as { state: string }).state
  }).toBe(expected)
}

async function capture(page: Page, testProject: string, name: string) {
  const allowed = testProject === 'desktop' || name === 'terminal'
  if (!allowed || process.env.ROBOM_E2E_CAPTURE === '0') return
  mkdirSync(screenshots, { recursive: true })
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({
    path: path.join(screenshots, `phase02-${name}-${testProject}.png`),
    fullPage: name !== 'terminal' || testProject !== 'desktop',
  })
}

async function expectImportantControls(page: Page) {
  const undersized = await page.locator('button:visible, select:visible').evaluateAll((controls) => (
    controls
      .map((control) => ({ label: control.textContent?.trim() || control.getAttribute('aria-label'), height: control.getBoundingClientRect().height }))
      .filter((control) => control.height < 48)
  ))
  expect(undersized).toEqual([])
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

test('PAPER 전용 UI와 제어·차트가 세 화면 크기에서 안정적으로 작동한다', async ({ page, request }, testInfo) => {
  const runtimeErrors: string[] = []
  const failedRequests: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') runtimeErrors.push(message.text())
  })
  page.on('pageerror', (error) => runtimeErrors.push(error.message))
  page.on('requestfailed', (request) => failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText ?? ''}`))

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'FlowScalper' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '자동 관찰 홈' })).toBeVisible()
  await expect(page.getByText('모의매매 · PAPER')).toBeVisible()
  await expect(page.getByText('실제 주문 0')).toBeVisible()
  await expect(page.getByText('실제 돈 움직이지 않음')).toBeVisible()
  await expect(page.getByText('샘플 화면').first()).toBeVisible()
  await expect(page.locator('input[type="password"]')).toHaveCount(0)
  await expect(page.getByText('6개 독립 전략 합계')).toBeVisible()
  await expect(page.getByText(/한 개의 실제 1,000 USDT 계좌 결과가 아닙니다/)).toBeVisible()
  await expectImportantControls(page)
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo.project.name, 'home')

  if (testInfo.project.name === 'desktop') {
    const startedAt = Date.now()
    const demoResponse = await request.post('/api/control/start-demo')
    expect(demoResponse.status()).toBe(202)
    expect(Date.now() - startedAt).toBeLessThan(1_000)
    const demoOperation = await demoResponse.json() as { operation_id: string }
    await waitForOperation(request, demoOperation.operation_id, 'COMPLETED')

    await page.getByRole('button', { name: '실제 공개시장으로 시작' }).click()
    await expect(page.getByRole('button', { name: '연결 취소' })).toBeVisible()
    const cancellable = await currentOperation(request)
    expect(terminalStates.has(cancellable.state)).toBe(false)
    await page.getByRole('button', { name: '연결 취소' }).click()
    await waitForOperation(request, cancellable.operation_id, 'CANCELLED')
    await expect(page.getByText('연결 작업을 취소했습니다')).toBeVisible()

    await page.getByRole('button', { name: '실제 공개시장으로 시작' }).click()
    await expect(page.locator('.operation-card h3')).toHaveText('E2E 재시도 가능한 공개시장 연결 오류입니다.')
    const failed = await currentOperation(request)
    expect(failed.state).toBe('FAILED_RETRYABLE')
    expect(failed.retryable).toBe(true)
    await page.getByRole('button', { name: '다시 시도' }).click()
    await expect.poll(async () => (await currentOperation(request)).operation_id).not.toBe(failed.operation_id)
    await expect(page.locator('.operation-card h3')).toHaveText('E2E 재시도 가능한 공개시장 연결 오류입니다.')
  }

  await page.getByRole('button', { name: '전략 리그', exact: true }).click()
  await expect(page.getByRole('heading', { name: '전략 리그' })).toBeVisible()
  await expect(page.locator('.strategy-card')).toHaveCount(6)
  await expect(page.locator('.strategy-card').nth(0).locator('.strategy-state')).toHaveText('리그 + 공동계좌')
  await expect(page.locator('.strategy-card').nth(1).locator('.strategy-state')).toHaveText('리그 + 공동계좌')
  for (const index of [2, 3, 4, 5]) await expect(page.locator('.strategy-card').nth(index).locator('.strategy-state')).toHaveText('리그에서만 테스트')
  await expect(page.getByText('기록만 하기')).toHaveCount(0)
  if (testInfo.project.name === 'desktop') {
    await page.locator('.strategy-card').first().getByRole('button', { name: '자세히 보기' }).click()
    await expect(page.getByRole('dialog', { name: '전략 상세 정보' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'BASE 가상계좌' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'STRESS 가상계좌' })).toBeVisible()
    await expect(page.getByText('평균 MAE · MFE').first()).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: '전략 상세 정보' })).toHaveCount(0)
  }
  await expectImportantControls(page)
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo.project.name, 'league')

  await page.getByRole('button', { name: '진행 거래', exact: true }).click()
  await expect(page.getByRole('heading', { name: '진행 거래' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'BASE' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByText(/실제 매수·매도는 없습니다/)).toBeVisible()
  await expectImportantControls(page)
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo.project.name, 'positions')

  await page.getByRole('button', { name: '고급 터미널', exact: true }).click()
  await expect(page.getByRole('heading', { name: '고급 터미널' })).toBeVisible()
  await expect(page.locator('.scanner-item')).toHaveCount(10)
  const rankToggle = page.getByRole('button', { name: '순위 고정' })
  await rankToggle.click()
  await expect(page.getByRole('button', { name: '순위 자동정렬' })).toHaveAttribute('aria-pressed', 'false')
  await page.getByRole('button', { name: '순위 자동정렬' }).click()
  const otherSymbol = page.locator('.scanner-symbol-button[aria-pressed="false"]').first()
  const otherSymbolName = (await otherSymbol.locator('strong').textContent()) ?? ''
  await otherSymbol.click()
  await expect(page.locator('.chart-panel h2')).toContainText(otherSymbolName)
  await expect(page.locator(`.scanner-item[data-row-key$=":${otherSymbolName}"] .scanner-symbol-button`)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.chart-wrap canvas').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'MA5', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'MA20', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'MA10', exact: true })).toHaveAttribute('aria-pressed', 'false')
  for (const indicator of ['MA10', 'EMA20', 'VWAP', '볼린저', 'RSI', 'MACD']) {
    const button = page.getByRole('button', { name: indicator, exact: true })
    await button.click()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
  }
  await page.getByLabel('차트 간격').selectOption('15')
  await expect(page.locator('.chart-panel h2')).toContainText('15s')
  await expect(page.getByText(/한국시간/).first()).toBeVisible()
  await expect(page.getByText('샘플 데이터 · LIVE 아님 · 한국시간')).toBeVisible()

  const initialChartBox = await page.locator('.chart-wrap').boundingBox()
  const initialScannerBox = await page.locator('.scanner-panel').boundingBox()
  await page.waitForTimeout(1_200)
  expect(await page.locator('.chart-wrap').boundingBox()).toEqual(initialChartBox)
  expect(await page.locator('.scanner-panel').boundingBox()).toEqual(initialScannerBox)

  await page.locator('.scanner-detail-button').first().click()
  await expect(page.getByRole('dialog', { name: '종목 상세 정보' })).toBeVisible()
  const chartWithDrawer = await page.locator('.chart-wrap').boundingBox()
  expect(chartWithDrawer?.width).toBe(initialChartBox?.width)
  expect(chartWithDrawer?.height).toBe(initialChartBox?.height)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '종목 상세 정보' })).toHaveCount(0)

  if (testInfo.project.name === 'desktop') {
    await page.getByLabel('차트 간격').selectOption('1')
    await expect(page.locator('.chart-panel h2')).toContainText('1s')
    const canvasBox = await page.locator('.chart-wrap canvas').first().boundingBox()
    expect(canvasBox).not.toBeNull()
    if (canvasBox) {
      const y = canvasBox.y + canvasBox.height / 2
      await page.mouse.move(canvasBox.x + canvasBox.width * 0.2, y)
      await page.mouse.down()
      await page.mouse.move(canvasBox.x + canvasBox.width * 0.8, y, { steps: 12 })
      await page.mouse.up()
      await expect(page.getByRole('button', { name: '현재로 돌아가기' })).toBeVisible()
      await page.getByRole('button', { name: '현재로 돌아가기' }).click()
      await expect(page.getByRole('button', { name: '현재로 돌아가기' })).toHaveCount(0)
    }
    await page.getByLabel('차트 간격').selectOption('15')
    await expect(page.locator('.chart-panel h2')).toContainText('15s')
  }

  await page.getByRole('button', { name: '전체화면' }).click()
  await expect(page.locator('.chart-panel')).toHaveClass(/chart-full-window/)
  await page.getByRole('button', { name: '전체화면 닫기' }).click()
  await expect(page.locator('.chart-panel')).not.toHaveClass(/chart-full-window/)
  await expectImportantControls(page)
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo.project.name, 'terminal')

  for (const [button, heading] of [
    ['거래 기록', '거래 기록'],
    ['과거 재생', '과거 데이터 다시 보기'],
    ['성과', '성과'],
    ['안전 설정', '안전 설정'],
    ['시스템', '시스템 상태'],
  ]) {
    await page.getByRole('button', { name: button, exact: true }).click()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expect(page.getByText('모의매매 · PAPER')).toBeVisible()
    await expectImportantControls(page)
    await expectNoHorizontalOverflow(page)
  }
  expect(runtimeErrors).toEqual([])
  expect(failedRequests).toEqual([])
})
