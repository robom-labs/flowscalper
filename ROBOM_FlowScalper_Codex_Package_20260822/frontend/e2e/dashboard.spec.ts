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
      session_version: 5,
      run_id: 'e2e-fixture',
      trade_id: 'e2e-focus-trade',
      profile: 'BASE',
      symbol: 'BTCUSDT',
      side: 'LONG',
      strategy_id: 'LSA_REVERSAL_V1',
      levels: { signal_ts_ms: 1_721_000_180_000, entry: '100.10', initial_stop: '99.55', take_profit_1: '101.40', take_profit_2: '102.00' },
      milestones: [
        { kind: 'SIGNAL', ts_ms: 1_721_000_180_000, price: '100.10', label: '진입 신호 확정' },
        { kind: 'ENTRY', ts_ms: 1_721_000_360_000, price: '100.10', label: 'PAPER 진입 체결' },
        { kind: 'TP1_HIT', ts_ms: 1_721_000_720_000, price: '101.40', label: 'TP1 도달' },
        { kind: 'EXIT', ts_ms: 1_721_001_080_000, price: '101.90', label: '실제 종료 · 익절' },
      ],
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
  if (testInfo.project.name !== 'desktop') {
    const summaryButton = await page.getByRole('button', { name: '요약' }).boundingBox()
    expect(summaryButton?.width).toBeGreaterThanOrEqual(48)
    expect(summaryButton?.height).toBeGreaterThanOrEqual(48)
    for (const button of await page.getByRole('navigation', { name: '주요 화면' }).getByRole('button').all()) {
      const box = await button.boundingBox()
      expect(box?.height).toBeGreaterThanOrEqual(48)
    }
  }
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

  if (testInfo.project.name !== 'desktop') {
    await page.locator('.market-toolbar').evaluate((toolbar) => {
      const notice = document.createElement('p')
      notice.className = 'market-notice focus-toast e2e-focus-notice'
      notice.textContent = '새 PAPER 진입 · 화면 겹침 검증'
      toolbar.after(notice)
    })
    const toolbar = await page.locator('.market-toolbar').boundingBox()
    const notice = await page.locator('.e2e-focus-notice').boundingBox()
    const operation = await page.locator('.operation-status-card').boundingBox()
    expect(notice?.y).toBeGreaterThanOrEqual((toolbar?.y ?? 0) + (toolbar?.height ?? 0))
    expect(operation?.y).toBeGreaterThanOrEqual((notice?.y ?? 0) + (notice?.height ?? 0))
    await page.locator('.e2e-focus-notice').evaluate((element) => element.remove())
  }

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
  if (testInfo.project.name !== 'desktop') {
    for (const button of await page.getByRole('navigation', { name: '하위 화면' }).getByRole('button').all()) {
      const box = await button.boundingBox()
      expect(box?.width).toBeGreaterThanOrEqual(48)
      expect(box?.height).toBeGreaterThanOrEqual(48)
    }
  }
  await expect(page.locator('.strategy-compact-table tbody tr')).toHaveCount(15)
  await expect(page.getByText('10개 감시 · 검증 중지 5개 · 문제 0개 · 실제 주문 0')).toBeVisible()
  await expect(page.getByText('준비 중')).toHaveCount(10)
  await expect(page.locator('.strategy-monitor.off')).toHaveCount(5)
  await expect(page.locator('.strategy-inline-modes button[aria-pressed="true"]')).toHaveCount(15)
  await expect(page.locator('.strategy-inline-directions button[aria-pressed="true"]')).toHaveCount(30)
  await expect(page.locator('.strategy-inline-directions button:disabled')).toHaveCount(10)
  await expect(page.getByRole('button', { name: 'LSA 반전 공동·독립 모의 중' })).toBeDisabled()
  await page.getByRole('button', { name: '자세히', exact: true }).first().click()
  const strategyDialog = page.getByRole('dialog', { name: '전략 상세 정보' })
  await expect(strategyDialog.getByText('최소 준비', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('무엇을 노리나요?', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('종료 원칙', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('필요 데이터', { exact: true })).toBeHidden()
  await strategyDialog.getByText('고급 기술 정보', { exact: true }).click()
  await expect(strategyDialog.getByText('필요 데이터', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('반증 조건', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('위험예산', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('대상 범위', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('미래정보 방지', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('연구 근거', { exact: true })).toBeVisible()
  await expect(strategyDialog.getByText('현재 상태 코드', { exact: true })).toBeVisible()
  await expect(strategyDialog).toContainText('SRC-BINANCE-DEPTH')
  await expect(strategyDialog).toContainText('공동 PAPER 0.10%·독립 PAPER 0.50%')
  await expect(page.getByText(/자산·순손익은 이번 실행, 완료 표본은 현재 전략 버전의 공개시장 모의거래 기준/).first()).toBeVisible()
  await expect(page.getByText('과거 버전 제외', { exact: true }).first()).toBeHidden()
  await page.getByText('고급 통계 보기', { exact: true }).first().click()
  await expect(page.getByText('과거 버전 제외', { exact: true }).first()).toBeVisible()
  await expect(strategyDialog.getByText('완료 거래', { exact: true }).first()).toBeVisible()
  await expect(page.getByText(/과거 변경 기록은 보존되지만 새 연구 승인 전에는 복원할 수 없습니다/)).toBeVisible()
  await page.getByText('변경 이력', { exact: true }).click()
  await expect(page.getByText(/급락·급등 쓸기 반전 전략 설정을 RETIRED·OFF 상태로 변경/)).toBeVisible()
  await expect(page.getByText(/NONE.*RECOVERY.*COST_ADJUSTED_RESEARCH_RETIREMENT/)).toBeVisible()
  await page.getByRole('button', { name: '전략 상세 정보 닫기' }).click()
  await page.getByRole('button', { name: '분석', exact: true }).click()
  await expect(page.getByText(/자산은 이번 실행, 승률과 통계는 현재 전략 버전/)).toBeVisible()
  await capturePhase09(page, testInfo.project.name, 'current-version-performance')
  await page.getByRole('button', { name: '전략별 종목' }).click()
  await expect(page.getByRole('heading', { name: '어떤 전략이 어떤 종목에 맞았나요?' })).toBeVisible()
  await expect(page.getByText(/과거 버전 154건 보관/)).toBeVisible()
  await expect(page.getByText('비교 기준 충족')).toBeVisible()
  if (testInfo.project.name === 'desktop') await expect(page.getByRole('columnheader', { name: '최종 순손익' })).toBeVisible()
  await expect(page.locator('.strategy-performance-panel tbody tr').first()).toContainText('+6 USDT')
  await expect(page.getByText('Profit Factor', { exact: false })).toHaveCount(0)
  await capturePhase09(page, testInfo.project.name, 'current-version-strategy-symbol')
  await page.getByRole('button', { name: '요약' }).click()
  await expect(page.getByRole('heading', { name: '프로그램 요약' })).toBeVisible()

  await page.getByRole('button', { name: '기록', exact: true }).click()
  await expect(page.getByRole('heading', { name: '거래 기록' })).toBeVisible()
  if (testInfo.project.name === 'desktop') {
    await expect(page.getByRole('columnheader', { name: '최종 결과' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '종료' })).toBeVisible()
  }
  await page.getByText('기록 범위 바꾸기', { exact: true }).click()
  await expect(page.getByLabel('Run 범위')).toHaveValue('CURRENT')
  await page.getByLabel('Run 범위').selectOption('ALL')
  const firstHistoryRow = page.locator('.history-table tbody tr').first()
  await firstHistoryRow.getByRole('button', { name: '자세히' }).click()
  const tradeDetail = page.locator('.trade-drawer')
  await expect(tradeDetail.getByText('진입 가격', { exact: true })).toBeVisible()
  await expect(tradeDetail.getByText('손절 가격', { exact: true })).toBeVisible()
  await expect(tradeDetail.getByText(/1차 목표|목표가\(과거 기록\)/).first()).toBeVisible()
  await expect(tradeDetail.getByText('최종 순손익', { exact: true })).toBeVisible()
  await expect(tradeDetail.getByText('거래 ID', { exact: true })).toBeHidden()
  await tradeDetail.getByText('기술 정보', { exact: true }).click()
  await expect(tradeDetail.getByText('거래 ID', { exact: true })).toBeVisible()
  await tradeDetail.getByRole('button', { name: '거래 상세 닫기' }).click()
  await page.locator('.history-table tbody tr').first().getByRole('button', { name: '다시보기' }).click()
  await expect(page.getByRole('heading', { name: /거래 집중 재생/ })).toBeVisible()
  await expect(page.locator('.focus-plan')).toContainText('저장 재생')
  await page.locator('.focus-replay-range input').fill('2')
  await expect(page.locator('.focus-replay-controls')).toContainText('3 / 6')
  await page.getByRole('button', { name: '처음', exact: true }).click()
  await page.getByRole('button', { name: '재생', exact: true }).click()
  await expect(page.locator('.focus-replay-controls')).not.toContainText('1 / 6', { timeout: 2_500 })
  await page.getByRole('button', { name: '일시정지', exact: true }).click()
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

  await page.getByRole('button', { name: '설정', exact: true }).click()
  await expect(page.getByRole('heading', { name: '시스템 상태' })).toBeVisible()
  const recoveryCard = page.locator('.system-summary-grid article').filter({ hasText: '마지막 시작 복구' })
  await expect(recoveryCard).toBeVisible()
  await expect(recoveryCard).toContainText(/신규 시작|샘플 상태 복구됨|상태 복구됨|복구 대기|안전 잠금/)
  const paperTransitionCard = page.locator('.system-summary-grid article').filter({ hasText: '마지막 PAPER 상태' })
  await expect(paperTransitionCard).toBeVisible()
  await expect(paperTransitionCard).toContainText(/아직 전환 없음|진입 대기|포지션 보호 중|청산 대기|거래 종료|대기 중/)
  await page.getByText('고급 진단 보기').click()
  await expect(page.getByText('시작 복구 결과')).toBeVisible()
  await expect(page.getByText(/^(NO_RECOVERY_NEEDED|FIXTURE_STATE_RECOVERED|RECOVERY_REVALIDATION_LOCKED|RECOVERY_DEFERRED|RECOVERY_FAIL_CLOSED)$/)).toBeVisible()
  await expect(page.getByText('마지막 PAPER 전환 결과')).toBeVisible()

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(errors).toEqual([])
})
