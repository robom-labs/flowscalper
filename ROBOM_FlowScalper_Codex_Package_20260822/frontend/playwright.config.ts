// PAPER 대시보드를 데스크톱·태블릿·모바일로 검증한다.
import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.ROBOM_E2E_PORT ?? '8876')
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'off',
  },
  webServer: {
    command:
      `cd .. && uv run python scripts/run_e2e_server.py --port ${port} --database /tmp/robom-flowscalper-e2e-wave06-${port}.sqlite3`,
    url: `${baseURL}/api/status`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1408, height: 714 }, deviceScaleFactor: 2 } },
    { name: 'tablet', use: { viewport: { width: 820, height: 1180 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true } },
  ],
})
