// Playwright에서 측정한 화면 준비시간과 접근성 검증값을 V6 증거 JSON에 원자적으로 합친다.
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'

type BrowserProjectEvidence = Record<string, unknown>

const evidencePath = path.resolve('..', 'evidence', 'V6_DASHBOARD_PAYLOAD_BENCHMARK.json')

export function recordBrowserEvidence(project: string, evidence: BrowserProjectEvidence) {
  const current = existsSync(evidencePath)
    ? JSON.parse(readFileSync(evidencePath, 'utf8')) as Record<string, unknown>
    : { schema_version: 2, status: 'NOT_RUN', scope: 'BROWSER_E2E_ONLY' }
  const priorBrowser = current.browser_e2e && typeof current.browser_e2e === 'object'
    ? current.browser_e2e as Record<string, unknown>
    : {}
  const priorProjects = priorBrowser.projects && typeof priorBrowser.projects === 'object'
    ? priorBrowser.projects as Record<string, unknown>
    : {}
  const priorProject = priorProjects[project] && typeof priorProjects[project] === 'object'
    ? priorProjects[project] as Record<string, unknown>
    : {}
  const projects = {
    ...priorProjects,
    [project]: { ...priorProject, ...evidence },
  }
  const allProjectsPassed = ['desktop', 'tablet', 'mobile'].every((name) => {
    const value = projects[name]
    return value && typeof value === 'object' && (value as Record<string, unknown>).status === 'PASS'
  })
  const browser = {
    ...priorBrowser,
    status: allProjectsPassed ? 'PASS' : 'PARTIAL',
    reason: undefined,
    scope: 'LOCAL_DEMO_FIXTURE_PLAYWRIGHT',
    measured_ts_utc: new Date().toISOString(),
    measurement_boundary: '브라우저 화면 준비와 DOM 접근성 검증이며 실제 공개시장 서비스 성능은 아닙니다.',
    projects,
  }
  const updated = { ...current, browser_e2e: browser }
  const temporaryPath = `${evidencePath}.${process.pid}.tmp`
  writeFileSync(temporaryPath, `${JSON.stringify(updated, null, 2)}\n`, 'utf8')
  renameSync(temporaryPath, evidencePath)
}
