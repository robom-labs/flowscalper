// 최신 localhost 8870 릴리스만 대상으로 V6 브라우저 감사 증거를 수집한다.
import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

const commonChecks = [
  'all_four_pages_rendered',
  'keyboard_navigation_passed',
  'escape_and_focus_restore_passed',
  'interactive_targets_48px_passed',
  'horizontal_overflow_zero',
  'console_errors_zero',
  'paper_safety_visible',
]

function auditGrep(checks: string[]) {
  return new RegExp(`audit:(?:${checks.join('|')})$`)
}

const artifactRoot = path.resolve('..', 'evidence', 'artifacts', 'v6_actual_8870_browser')

export default defineConfig({
  testDir: './e2e',
  testMatch: 'audit-8870.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  outputDir: path.join(artifactRoot, 'playwright-results'),
  reporter: [['json', { outputFile: path.join(artifactRoot, 'playwright.json') }]],
  use: {
    baseURL: 'http://127.0.0.1:8870',
    trace: 'off',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'desktop',
      grep: auditGrep([...commonChecks, 'desktop_project_passed', 'zoom_200_percent_reflow_passed']),
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1408, height: 900 },
        deviceScaleFactor: 1,
      },
    },
    {
      name: 'tablet',
      grep: auditGrep([...commonChecks, 'tablet_project_passed']),
      use: { viewport: { width: 820, height: 1180 }, deviceScaleFactor: 1 },
    },
    {
      name: 'mobile',
      grep: auditGrep([...commonChecks, 'mobile_project_passed']),
      use: {
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
        isMobile: true,
      },
    },
  ],
})
