// 3차 시장 중심 UI, compact 전략표와 관찰전용 Upbit를 실제 브라우저로 검증한다.
import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const screenshots = path.resolve('..', 'evidence', 'screenshots')

function candles(symbol: string) {
  const base = symbol.startsWith('KRW-') ? 80_000_000 : 60_000
  return Array.from({ length: 200 }, (_, index) => {
    const close = base + index * (base / 100_000)
    return { time: 1_721_000_000 + index * 180, open_ts_ms: (1_721_000_000 + index * 180) * 1_000, open: close - 2, high: close + 4, low: close - 4, close, volume: 100 + index, trade_count: 10 + index }
  })
}

async function installMarketFixtures(page: Page) {
  const binance = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', ...Array.from({ length: 137 }, (_, index) => `ASSET${String(index).padStart(3, '0')}USDT`)].map((symbol, index) => ({ venue: 'BINANCE_USDM', symbol, display_symbol: symbol.replace('USDT', '/USDT'), base_asset: symbol.replace('USDT', ''), quote_asset: 'USDT', market_role: 'PAPER_EXECUTION', last: 60_000 - index, bid: 59_999, ask: 60_001, change_percent: 1.2, quote_volume_24h: 1_000_000_000 - index, trade_count_24h: 10_000, status: 'ACTIVE', strategy_eligible: true }))
  const upbit = Array.from({ length: 90 }, (_, index) => ({ venue: 'UPBIT_KRW', symbol: index === 0 ? 'KRW-BTC' : `KRW-COIN${String(index).padStart(2, '0')}`, display_symbol: index === 0 ? 'BTC/KRW' : `COIN${index}/KRW`, korean_name: index === 0 ? '비트코인' : `테스트코인 ${index}`, english_name: index === 0 ? 'Bitcoin' : `Test Coin ${index}`, base_asset: index === 0 ? 'BTC' : `COIN${index}`, quote_asset: 'KRW', market_role: 'OBSERVATION_ONLY', last: 80_000_000 - index, bid: 0, ask: 0, change_percent: 0.5, quote_volume_24h: 900_000_000 - index, trade_count_24h: 0, status: 'ACTIVE', strategy_eligible: false }))
  await page.route('**/api/markets/catalog**', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ source: 'ALL_PUBLIC', count: 230, rows: [...binance, ...upbit], counts: { BINANCE_USDM: 140, UPBIT_KRW: 90, total: 230 }, paper_execution_venue: 'BINANCE_USDM', observation_only_venues: ['UPBIT_KRW'], auth_required: false, real_orders_enabled: false }) }))
  await page.route('**/api/markets/candles**', (route) => {
    const url = new URL(route.request().url())
    const source = url.searchParams.get('source') ?? 'BINANCE_USDM'
    const symbol = url.searchParams.get('symbol') ?? 'BTCUSDT'
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ source, symbol, interval_seconds: 180, candles: candles(symbol), ticker: {}, observation_only: source === 'UPBIT_KRW', auth_required: false, real_orders_enabled: false }) })
  })
  await page.route('**/api/markets/select', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ auth_required: false, real_orders_enabled: false }) }))
  await page.route('**/api/analytics/strategy-symbols', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ generated_ts_ms: 1_721_000_000_000, rows: [{ strategy_id: 'LSA_REVERSAL_V1', profile: 'BASE', symbol: 'BTCUSDT', sample_size: 30, sample_status: 'RESEARCH_SAMPLE', ranking_eligible: true, rank_score: 1, rank: 1, win_rate: '0.6', expectancy_usdt: '0.2', profit_factor: '1.4', fees: '1', slippage: '1', net_pnl: '6', maximum_drawdown: '2', analysis_scope: 'CURRENT_STRATEGY_VERSION', strategy_version: 'e2e-current', excluded_prior_version_samples: 21 }], ranking_rule: '표본 30건 이상', analysis_scope: 'CURRENT_STRATEGY_VERSION', strategy_version: 'e2e-current', excluded_prior_version_samples: 154, auth_required: false, real_orders_enabled: false }) }))
  await page.route('**/api/replay/*/focus**', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      session_version: 1,
      run_id: 'e2e-fixture',
      trade_id: 'e2e-focus-trade',
      profile: 'BASE',
      symbol: 'BTCUSDT',
      side: 'LONG',
      strategy_id: 'LSA_REVERSAL_V1',
      start_ts_ms: 1_721_000_000_000,
      entry_ts_ms: 1_721_000_360_000,
      exit_ts_ms: 1_721_001_080_000,
      end_ts_ms: 1_721_001_080_000,
      default_speed: 5,
      speeds: [0.5, 1, 2, 5, 10, 20, 40, 80],
      frames: [
        { ts_ms: 1_721_000_000_000, event_id: 'pre-1', event_type: 'BOOK_TICKER', data: { bid: '99.80', ask: '99.82', mid: '99.81' }, phase: 'PRE_ENTRY', markers: [], fills: [] },
        { ts_ms: 1_721_000_180_000, event_id: 'pre-2', event_type: 'DEPTH_UPDATE', data: { bid: '99.95', ask: '99.97', mid: '99.96' }, phase: 'PRE_ENTRY', markers: [], fills: [] },
        { ts_ms: 1_721_000_360_000, event_id: 'open-1', event_type: 'TRADE', data: { bid: '100.09', ask: '100.11', mid: '100.10' }, phase: 'OPEN', markers: [{ kind: 'ENTRY', ts_ms: 1_721_000_360_000, price: '100.10', label: 'PAPER 진입 체결' }], fills: [] },
        { ts_ms: 1_721_000_540_000, event_id: 'open-2', event_type: 'BOOK_TICKER', data: { bid: '100.74', ask: '100.76', mid: '100.75' }, phase: 'OPEN', markers: [{ kind: 'ENTRY', ts_ms: 1_721_000_360_000, price: '100.10', label: 'PAPER 진입 체결' }], fills: [] },
        { ts_ms: 1_721_000_720_000, event_id: 'open-3', event_type: 'DEPTH_UPDATE', data: { bid: '101.39', ask: '101.41', mid: '101.40' }, phase: 'OPEN', markers: [{ kind: 'ENTRY', ts_ms: 1_721_000_360_000, price: '100.10', label: 'PAPER 진입 체결' }], fills: [] },
        { ts_ms: 1_721_001_080_000, event_id: 'closed-1', event_type: 'TRADE', data: { bid: '101.89', ask: '101.91', mid: '101.90' }, phase: 'CLOSED', markers: [{ kind: 'ENTRY', ts_ms: 1_721_000_360_000, price: '100.10', label: 'PAPER 진입 체결' }, { kind: 'EXIT', ts_ms: 1_721_001_080_000, price: '101.90', label: 'PAPER 종료 체결' }], fills: [] },
      ],
      candles: candles('BTCUSDT').map((_candle, index) => {
        const open = 98.8 + index * 0.01
        return { time: 1_720_978_580 + index * 180, open_ts_ms: (1_720_978_580 + index * 180) * 1_000, open, high: open + 0.08, low: open - 0.08, close: open + 0.03, volume: 20 + index, trade_count: 5 + index }
      }),
      keyframes: [{ frame_index: 0, ts_ms: 1_721_000_000_000 }, { frame_index: 5, ts_ms: 1_721_001_080_000 }],
      trade: {}, fills: [], profile_comparison: [],
      reconciliation: { applicable: false, sample_type: 'DEMO_FIXTURE', matched: null, reason: 'OFFLINE_FIXTURE_UI_ONLY', replay_checksum: 'e2e-replay-checksum', replay_final_state: 'CLOSED' },
      checksum: '86d0b3c10f20e2e5phase03positionfocus',
      paper_only: true, real_orders_enabled: false, auth_required: false,
    }),
  }))
}

async function capture(page: Page, project: string, name: string) {
  if (process.env.ROBOM_E2E_CAPTURE === '0') return
  mkdirSync(screenshots, { recursive: true })
  await page.screenshot({ path: path.join(screenshots, `phase03-${name}-${project}.png`), fullPage: false })
}

async function capturePhase09(page: Page, project: string, name: string) {
  if (process.env.ROBOM_E2E_CAPTURE === '0') return
  mkdirSync(screenshots, { recursive: true })
  await page.screenshot({ path: path.join(screenshots, `phase09-${name}-${project}.png`), fullPage: false })
}

test('시장 중심 PAPER 화면이 데스크톱·태블릿·모바일에서 안정적이다', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('pageerror', (error) => errors.push(error.message))
  await installMarketFixtures(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'BTCUSDT 시장' })).toBeVisible()
  await expect(page.getByText('샘플 PAPER · LIVE 아님 · 실제 주문 0')).toBeVisible()
  await expect(page.getByLabel('프로그램 작동 상태')).toContainText('샘플 작동 중')
  await expect(page.getByLabel('프로그램 작동 상태')).toContainText('시장 관찰계속 작동')
  await expect(page.getByRole('navigation', { name: '주요 화면' }).getByRole('button')).toHaveCount(5)
  await expect(page.getByText('전략 리그')).toHaveCount(0)
  await expect(page.getByText('고급 터미널')).toHaveCount(0)
  if (testInfo.project.name === 'desktop') {
    await expect(page.locator('.market-rail')).toBeVisible()
  } else {
    await page.getByRole('button', { name: '종목', exact: true }).click()
    await expect(page.getByRole('dialog', { name: '종목 목록' }).locator('.market-rail')).toBeVisible()
  }
  await expect(page.locator('.chart-wrap canvas').first()).toBeVisible()
  await expect(page.getByLabel('차트 시간')).toHaveValue('180')
  await expect(page.locator('.market-row:visible')).toHaveCount(40)
  if (testInfo.project.name !== 'desktop') await page.getByRole('dialog', { name: '종목 목록' }).getByRole('button', { name: '닫기', exact: true }).click()
  await expect(page.locator('.indicator-popover')).not.toHaveAttribute('open', '')

  const chartBefore = await page.locator('.chart-panel').boundingBox()
  await page.locator('.indicator-popover summary').click()
  await expect(page.getByRole('button', { name: 'MA5', exact: true })).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('button', { name: 'MA10', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'MA20', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'RSI', exact: true }).click()
  await page.getByRole('button', { name: 'RSI', exact: true }).click()
  expect(await page.locator('.chart-panel').boundingBox()).toEqual(chartBefore)
  await page.locator('.indicator-popover summary').click()
  await page.getByRole('button', { name: '전체화면', exact: true }).click()
  await expect(page.locator('.chart-panel')).toHaveClass(/chart-full-window/)
  await expect(page.getByRole('button', { name: '전체화면 닫기', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '전체화면 닫기', exact: true }).click()
  await expect(page.locator('.chart-panel')).not.toHaveClass(/chart-full-window/)

  if (testInfo.project.name === 'desktop') {
    const header = await page.locator('.topbar').boundingBox()
    expect(header?.height).toBeLessThanOrEqual(52)
    expect(chartBefore?.width).toBeGreaterThanOrEqual(1050)
    expect(chartBefore?.height).toBeGreaterThanOrEqual(680)
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true)
  }
  const stable = await page.locator('.chart-panel').boundingBox()
  await page.waitForTimeout(1_200)
  expect(await page.locator('.chart-panel').boundingBox()).toEqual(stable)
  await capture(page, testInfo.project.name, 'market')

  if (testInfo.project.name !== 'mobile') {
    if (testInfo.project.name === 'tablet') await page.getByRole('button', { name: '종목', exact: true }).click()
    const rail = testInfo.project.name === 'tablet' ? page.getByRole('dialog', { name: '종목 목록' }) : page.locator('.market-grid .market-rail')
    await rail.getByRole('button', { name: '원화 참고' }).click()
    await rail.getByLabel('종목 검색').fill('비트코인')
    await rail.getByRole('button', { name: /비트코인/ }).click()
    await expect(page.getByRole('heading', { name: 'KRW-BTC 시장' })).toBeVisible()
    await expect(page.getByText(/관찰 전용 · KRW 현물 공개시세/)).toBeVisible()
  }

  await page.getByRole('button', { name: '전략', exact: true }).click()
  await expect(page.getByRole('heading', { name: '전략 설정' })).toBeVisible()
  await expect(page.locator('.strategy-compact-table tbody tr')).toHaveCount(9)
  await expect(page.getByText('9/9 전략 켜짐 · 실제 주문 0')).toBeVisible()
  await expect(page.locator('.strategy-inline-modes button[aria-pressed="true"]')).toHaveCount(9)
  await expect(page.locator('.strategy-inline-directions button[aria-pressed="true"]')).toHaveCount(18)
  await page.getByRole('button', { name: '자세히', exact: true }).first().click()
  await expect(page.getByText(/현재 전략 버전의 공개시장 PAPER 기준/).first()).toBeVisible()
  await expect(page.getByText('과거 버전 제외', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('승 · 패 · 보합', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '전략 상세 정보 닫기' }).click()
  await page.getByRole('button', { name: '분석', exact: true }).click()
  await expect(page.getByText(/현재 전략 버전의 공개시장 PAPER만 집계/)).toBeVisible()
  await capturePhase09(page, testInfo.project.name, 'current-version-performance')
  await page.getByRole('button', { name: '전략별 종목' }).click()
  await expect(page.getByRole('heading', { name: '전략별 종목 성과' })).toBeVisible()
  await expect(page.getByText(/과거 버전 154건 제외/)).toBeVisible()
  await expect(page.getByText('연구 순위 포함')).toBeVisible()
  await capturePhase09(page, testInfo.project.name, 'current-version-strategy-symbol')
  await page.getByRole('button', { name: '요약' }).click()
  await expect(page.getByRole('heading', { name: '프로그램 요약' })).toBeVisible()

  await page.getByRole('button', { name: '기록', exact: true }).click()
  await expect(page.getByRole('heading', { name: '거래 기록' })).toBeVisible()
  await expect(page.getByLabel('Run 범위')).toHaveValue('CURRENT')
  await page.getByLabel('Run 범위').selectOption('ALL')
  await page.locator('.history-table tbody tr').first().getByRole('button', { name: '재생' }).click()
  await expect(page.getByRole('heading', { name: /거래 집중 재생/ })).toBeVisible()
  await page.locator('.focus-replay-range input').fill('2')
  await expect(page.locator('.focus-replay-controls')).toContainText('3 / 6')
  const focusChartBeforeSheet = await page.locator('.focus-grid .chart-panel').boundingBox()
  if (testInfo.project.name === 'desktop') {
    const focusChart = await page.locator('.focus-grid .chart-panel').boundingBox()
    expect(focusChart?.width).toBeGreaterThanOrEqual(960)
    expect(focusChart?.height).toBeGreaterThanOrEqual(780)
    await capture(page, testInfo.project.name, 'position-focus')
    await capture(page, testInfo.project.name, 'replay-position-focus')
    await page.getByLabel('속도').selectOption('80')
    await expect(page.getByText('빨리감기 · 80배속')).toBeVisible()
    await capture(page, testInfo.project.name, 'replay-position-focus-80x')
  } else {
    await page.getByRole('button', { name: '계획', exact: true }).click()
    await expect(page.getByRole('dialog', { name: '진입 계획 상세' })).toBeVisible()
    expect(await page.locator('.focus-grid .chart-panel').boundingBox()).toEqual(focusChartBeforeSheet)
    await page.getByRole('dialog', { name: '진입 계획 상세' }).getByRole('button', { name: '닫기', exact: true }).click()
    await capture(page, testInfo.project.name, 'position-focus')
  }

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(errors).toEqual([])
})
