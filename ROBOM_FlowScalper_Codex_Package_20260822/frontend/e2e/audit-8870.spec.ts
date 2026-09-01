// 최신 8870 PAPER 릴리스의 네 화면·접근성·안전 상태를 체크 ID별로 검증한다.
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const projectRoot = path.resolve('..')
const screenshotRoot = path.join(projectRoot, 'evidence', 'screenshots', 'v6-actual-8870')

type AuditPage = {
  errors: string[]
}

async function openAuditPage(page: Page): Promise<AuditPage> {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('navigation', { name: '주요 화면' })).toBeVisible()
  await expect(page.getByText('PAPER · 실제 주문 0')).toBeVisible()
  return { errors }
}

async function captureAudit(
  page: Page,
  testInfo: TestInfo,
  checkId: string,
  auditPage: AuditPage,
) {
  expect(auditPage.errors, '브라우저 console/page 오류가 없어야 합니다.').toEqual([])
  mkdirSync(screenshotRoot, { recursive: true })
  const absolutePath = path.join(
    screenshotRoot,
    `${checkId}-${testInfo.project.name}.png`,
  )
  await page.evaluate(({ checkId: markerCheckId, project }) => {
    const marker = document.createElement('div')
    marker.id = 'robom-audit-evidence-marker'
    marker.textContent = `ROBOM audit evidence · ${markerCheckId} · ${project}`
    Object.assign(marker.style, {
      position: 'fixed',
      right: '8px',
      bottom: '8px',
      zIndex: '2147483647',
      padding: '4px 8px',
      border: '1px solid #111827',
      borderRadius: '4px',
      background: '#f8fafc',
      color: '#111827',
      font: '12px/1.4 monospace',
    })
    document.body.append(marker)
  }, { checkId, project: testInfo.project.name })
  try {
    await page.screenshot({ path: absolutePath, fullPage: false })
  } finally {
    await page.locator('#robom-audit-evidence-marker').evaluate((marker) => marker.remove())
  }
  const relativePath = path.relative(projectRoot, absolutePath).split(path.sep).join('/')
  testInfo.attachments.push({
    name: `${checkId}-${testInfo.project.name}`,
    contentType: 'image/png',
    path: relativePath,
  })
}

async function openPageByName(page: Page, name: '시장' | '전략' | '거래' | '설정') {
  await page
    .getByRole('navigation', { name: '주요 화면' })
    .getByRole('button', { name, exact: true })
    .click()
}

test('audit:all_four_pages_rendered', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  await expect(page.getByRole('heading', { name: /시장$/ })).toBeVisible()
  await openPageByName(page, '전략')
  await expect(page.getByRole('heading', { name: '전략 한눈에 보기' })).toBeVisible()
  await openPageByName(page, '거래')
  await expect(page.getByRole('heading', { name: '거래', exact: true })).toBeVisible()
  await openPageByName(page, '설정')
  await expect(page.getByRole('heading', { name: '설정', exact: true })).toBeVisible()
  await captureAudit(page, testInfo, 'all_four_pages_rendered', auditPage)
})

test('audit:desktop_project_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  expect(page.viewportSize()).toEqual({ width: 1408, height: 900 })
  await expect(page.locator('.market-rail')).toBeVisible()
  await captureAudit(page, testInfo, 'desktop_project_passed', auditPage)
})

test('audit:tablet_project_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  expect(page.viewportSize()).toEqual({ width: 820, height: 1180 })
  await expect(page.getByRole('button', { name: '종목', exact: true })).toBeVisible()
  await captureAudit(page, testInfo, 'tablet_project_passed', auditPage)
})

test('audit:mobile_project_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  expect(page.viewportSize()).toEqual({ width: 390, height: 844 })
  await expect(page.getByRole('button', { name: '종목', exact: true })).toBeVisible()
  await captureAudit(page, testInfo, 'mobile_project_passed', auditPage)
})

test('audit:keyboard_navigation_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  const navigation = page.getByRole('navigation', { name: '주요 화면' })
  const market = navigation.getByRole('button', { name: '시장', exact: true })
  const strategies = navigation.getByRole('button', { name: '전략', exact: true })
  await market.focus()
  await page.keyboard.press('Tab')
  await expect(strategies).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '전략 한눈에 보기' })).toBeVisible()
  for (const pageName of ['시장', '전략', '거래', '설정'] as const) {
    await openPageByName(page, pageName)
    await expect(page.getByRole('main')).toBeVisible()
    const unnamedTargets = await page
      .locator('button,input,select,summary,a[href]')
      .evaluateAll((elements) => elements
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          const style = window.getComputedStyle(element)
          return rect.width > 0
            && rect.height > 0
            && style.visibility !== 'hidden'
            && style.display !== 'none'
        })
        .filter((element) => {
          const labelledBy = element.getAttribute('aria-labelledby')
          const labelledByValid = Boolean(labelledBy)
            && labelledBy!.split(/\s+/).every((id) => Boolean(document.getElementById(id)?.textContent?.trim()))
          const explicitName = Boolean(element.getAttribute('aria-label')?.trim()) || labelledByValid
          if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
            return !(explicitName || (element.labels?.length ?? 0) > 0)
          }
          return !(
            explicitName
            || element.getAttribute('title')?.trim()
            || element.textContent?.trim()
          )
        })
        .map((element) => element.outerHTML))
    expect(unnamedTargets, `${pageName} 화면의 모든 action에 접근 가능한 이름이 있어야 합니다.`).toEqual([])
  }
  await captureAudit(page, testInfo, 'keyboard_navigation_passed', auditPage)
})

test('audit:escape_and_focus_restore_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  await openPageByName(page, '전략')
  const trigger = page
    .locator('.strategy-compact-table tbody tr')
    .first()
    .getByRole('button', { name: '자세히·설정' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: '전략 상세 정보' })
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
  await captureAudit(page, testInfo, 'escape_and_focus_restore_passed', auditPage)
})

test('audit:interactive_targets_48px_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  for (const pageName of ['시장', '전략', '거래', '설정'] as const) {
    await openPageByName(page, pageName)
    const targets = await page
      .locator('button,input,select,summary,a[href]')
      .evaluateAll((elements) => elements
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          const style = window.getComputedStyle(element)
          return rect.width > 0
            && rect.height > 0
            && style.visibility !== 'hidden'
            && style.display !== 'none'
            && element.getAttribute('aria-disabled') !== 'true'
            && !('disabled' in element && element.disabled === true)
        })
        .map((element) => {
          const rect = element.getBoundingClientRect()
          return {
            name: element.getAttribute('aria-label') || element.textContent?.trim() || element.tagName,
            width: rect.width,
            height: rect.height,
          }
        }))
    expect(targets.length).toBeGreaterThan(0)
    expect(
      targets.filter((target) => target.width < 48 || target.height < 48),
      `${pageName} 화면의 모든 action target은 가로·세로 48px 이상이어야 합니다.`,
    ).toEqual([])
  }
  await captureAudit(page, testInfo, 'interactive_targets_48px_passed', auditPage)
})

test('audit:horizontal_overflow_zero', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  for (const pageName of ['시장', '전략', '거래', '설정'] as const) {
    await openPageByName(page, pageName)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      `${pageName} 화면에 document-level 가로 overflow가 없어야 합니다.`,
    ).toBe(true)
  }
  await captureAudit(page, testInfo, 'horizontal_overflow_zero', auditPage)
})

test('audit:console_errors_zero', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  await page.waitForTimeout(1_000)
  expect(auditPage.errors).toEqual([])
  await captureAudit(page, testInfo, 'console_errors_zero', auditPage)
})

test('audit:zoom_200_percent_reflow_passed', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  await page.setViewportSize({ width: 704, height: 450 })
  for (const pageName of ['시장', '전략', '거래', '설정'] as const) {
    await openPageByName(page, pageName)
    await expect(page.getByRole('navigation', { name: '주요 화면' })).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      `200% reflow 상당 폭의 ${pageName} 화면에 가로 overflow가 없어야 합니다.`,
    ).toBe(true)
  }
  await page.setViewportSize({ width: 1408, height: 900 })
  await captureAudit(page, testInfo, 'zoom_200_percent_reflow_passed', auditPage)
})

test('audit:paper_safety_visible', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  await expect(page.getByText('PAPER · 실제 주문 0')).toBeVisible()
  const dashboard = await page.evaluate(async () => {
    const response = await fetch('/api/dashboard')
    if (!response.ok) throw new Error(`dashboard HTTP ${response.status}`)
    return response.json() as Promise<Record<string, unknown>>
  })
  const status = dashboard.status as Record<string, unknown>
  const risk = dashboard.risk as Record<string, unknown>
  const system = dashboard.system as Record<string, unknown>
  expect(status.execution_state).toBe('PAPER')
  expect({
    paper_only: risk.paper_only,
    real_orders_enabled: status.real_orders_enabled,
    auth_required: status.auth_required,
    private_api_enabled: system.private_api_enabled,
    api_key_enabled: system.api_key_enabled,
    wallet_enabled: system.wallet_enabled,
    runtime_ai_order_decision_enabled: system.runtime_ai_order_decision_enabled,
    funding_readiness: system.funding_readiness,
  }).toEqual({
    paper_only: true,
    real_orders_enabled: false,
    auth_required: false,
    private_api_enabled: false,
    api_key_enabled: false,
    wallet_enabled: false,
    runtime_ai_order_decision_enabled: false,
    funding_readiness: 'NOT_READY',
  })
  expect(system.release_isolated).toBe(true)
  expect(system.release_commit).toMatch(/^[0-9a-f]{40}$/)
  await captureAudit(page, testInfo, 'paper_safety_visible', auditPage)
})

test('audit:continuous_entry_and_leverage_visible', async ({ page }, testInfo) => {
  const auditPage = await openAuditPage(page)
  await expect(page.getByText('자동 진입 · 항상 허용')).toBeVisible()
  await expect(page.getByRole('button', { name: '새 진입 잠시 멈추기' })).toHaveCount(0)
  await openPageByName(page, '설정')
  const leverage = page.getByRole('combobox', { name: 'PAPER 레버리지' })
  await expect(leverage).toHaveValue('10')
  await expect(leverage.locator('option[value="100"]')).toHaveText('100배')
  const configuration = await page.evaluate(async () => {
    const response = await fetch('/api/dashboard')
    if (!response.ok) throw new Error(`dashboard HTTP ${response.status}`)
    const dashboard = await response.json() as Record<string, unknown>
    return dashboard.paper_research_configuration as Record<string, unknown>
  })
  expect({
    selected_leverage: configuration.selected_leverage,
    maximum_available_leverage: configuration.maximum_available_leverage,
    continuous_entry_mode: configuration.continuous_entry_mode,
    daily_trade_limit_enabled: configuration.daily_trade_limit_enabled,
    daily_loss_lock_enabled: configuration.daily_loss_lock_enabled,
    weekly_loss_lock_enabled: configuration.weekly_loss_lock_enabled,
    loss_cooldown_enabled: configuration.loss_cooldown_enabled,
    paper_only: configuration.paper_only,
    real_orders_enabled: configuration.real_orders_enabled,
  }).toEqual({
    selected_leverage: 10,
    maximum_available_leverage: 100,
    continuous_entry_mode: true,
    daily_trade_limit_enabled: false,
    daily_loss_lock_enabled: false,
    weekly_loss_lock_enabled: false,
    loss_cooldown_enabled: false,
    paper_only: true,
    real_orders_enabled: false,
  })
  await captureAudit(page, testInfo, 'continuous_entry_and_leverage_visible', auditPage)
})
