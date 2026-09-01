// 시장을 기본으로 V6 네 화면과 PAPER 제어 상태를 조합하는 애플리케이션 루트다.
import { lazy, Suspense, useState } from 'react'
import { Navigation } from './components/Navigation'
import { SafetyHeader } from './components/SafetyHeader'
import { useDashboard } from './hooks/useDashboard'
import { MarketPage } from './pages/MarketPage'
import { compareReleaseCommits, readFrontendReleaseCommit } from './releaseCompatibility'
import type { PageId } from './types'

const StrategiesPage = lazy(() => import('./pages/StrategiesPage').then((module) => ({ default: module.StrategiesPage })))
const TradesPage = lazy(() => import('./pages/TradesPage').then((module) => ({ default: module.TradesPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const researchPreferenceKey = 'robom.flowscalper.research-details'

function initialResearchDetails() {
  try { return globalThis.localStorage?.getItem(researchPreferenceKey) === '1' } catch { return false }
}

export default function App() {
  const [page, setPage] = useState<PageId>('market')
  const [researchDetails, setResearchDetails] = useState(initialResearchDetails)
  const {
    data,
    connected,
    safetyVerified,
    connectionState,
    bootstrapState,
    lastUpdateMs,
    connectionError,
    requestError,
    busyAction,
    immediateBusyAction,
    controlOperation,
    control,
    cancelControl,
    retryControl,
    selectChart,
    configureStrategy,
    rollbackStrategy,
    configureStrategyFamilyResearch,
    selectedFamilyDetail,
    selectStrategyFamily,
    clearError,
  } = useDashboard(page)
  const releaseCompatibility = compareReleaseCommits(
    readFrontendReleaseCommit(),
    data.system.release_commit,
  )

  if (bootstrapState === 'READY' && !releaseCompatibility.compatible) {
    return (
      <main className="release-mismatch-shell">
        <section className="release-mismatch-card" role="alert" aria-labelledby="release-mismatch-title">
          <p className="eyebrow">PAPER SAFETY LOCK</p>
          <h1 id="release-mismatch-title">프로그램 버전이 서로 맞지 않습니다.</h1>
          <p>화면과 서버가 서로 다른 버전이라 새 PAPER 작업과 화면 이동을 안전하게 막았습니다.</p>
          <dl>
            <div><dt>화면 버전</dt><dd>{releaseCompatibility.frontendCommit.slice(0, 12)}</dd></div>
            <div><dt>서버 버전</dt><dd>{releaseCompatibility.backendCommit.slice(0, 12)}</dd></div>
          </dl>
          <p className="release-mismatch-help">서비스 업데이트가 끝난 뒤 새로고침하세요. 실제 주문은 계속 0입니다.</p>
          <button type="button" onClick={() => window.location.reload()}>새로고침</button>
        </section>
      </main>
    )
  }

  const runControl = async (action: 'pause' | 'resume' | 'emergency-close' | 'new-run' | 'start-live' | 'start-demo') => {
    try {
      clearError()
      await control(action)
    } catch {
      // useDashboard가 서버의 실제 한국어 오류를 화면 상태로 보존한다.
    }
  }
  const pauseToggle = () => {
    const action = data.operation_status.recommended_action
    if (action === 'PAUSE' || action === 'RESUME') {
      void runControl(action === 'PAUSE' ? 'pause' : 'resume')
    }
  }
  const newRun = () => {
    if (window.confirm('기존 Run 기록을 보존하고 새 PAPER Run을 만들까요?')) void runControl('new-run')
  }
  const changeChart = async (symbol: string, intervalSeconds: number) => {
    try {
      clearError()
      await selectChart(symbol, intervalSeconds)
    } catch {
      // 오류 본문은 useDashboard가 requestError로 표시한다.
    }
  }
  const changeStrategy = async (
    strategyId: string,
    configuration: { mode: 'ACTIVE' | 'SHADOW' | 'OFF'; long_enabled: boolean; short_enabled: boolean; expected_revision: number },
  ) => {
    try {
      clearError()
      return await configureStrategy(strategyId, configuration)
    } catch {
      return null
    }
  }
  const undoStrategy = async (strategyId: string, targetRevision: number, expectedRevision: number) => {
    try {
      clearError()
      return await rollbackStrategy(strategyId, targetRevision, expectedRevision)
    } catch {
      return null
    }
  }
  const cancelOperation = async () => {
    try { await cancelControl() } catch { /* useDashboard가 오류를 표시한다. */ }
  }
  const retryOperation = async () => {
    try { await retryControl() } catch { /* useDashboard가 오류를 표시한다. */ }
  }
  const changePage = (nextPage: PageId) => {
    if (nextPage !== 'strategies') selectStrategyFamily(null)
    setPage(nextPage)
    window.scrollTo(0, 0)
  }
  const changeResearchDetails = (enabled: boolean) => {
    setResearchDetails(enabled)
    try { globalThis.localStorage?.setItem(researchPreferenceKey, enabled ? '1' : '0') } catch { /* 로컬 저장 실패는 PAPER 실행 설정에 영향이 없다. */ }
  }
  const globalError = requestError || connectionError || (bootstrapState === 'ERROR' ? '프로그램 서버에 연결하지 못했습니다. 실행 상태를 확인하세요.' : '')

  return (
    <main className="app-shell">
      <SafetyHeader data={data} connected={connected} safetyVerified={safetyVerified} connectionState={connectionState} onPauseToggle={pauseToggle} immediateAction={immediateBusyAction} />
      <Navigation page={page} onChange={changePage} />
      {globalError ? <p className="connection-error" role="alert">{globalError}</p> : null}
      {bootstrapState === 'LOADING' ? <p className="bootstrap-state" role="status">프로그램 상태를 불러오는 중입니다.</p> : null}
      <Suspense fallback={<p className="bootstrap-state" role="status" aria-live="polite">화면을 불러오는 중입니다.</p>}>
        {page === 'market' ? <MarketPage data={data} onChartChange={(symbol, interval) => void changeChart(symbol, interval)} onStartLive={() => void runControl('start-live')} onStartDemo={() => void runControl('start-demo')} busy={!connected || !safetyVerified || busyAction !== null || immediateBusyAction !== null || Boolean(controlOperation && !['COMPLETED', 'FAILED_RETRYABLE', 'FAILED_BLOCKED', 'CANCELLED'].includes(controlOperation.state))} operation={controlOperation} onCancel={() => void cancelOperation()} onRetry={() => void retryOperation()} /> : null}
        {page === 'strategies' ? <StrategiesPage data={data} history={data.history} strategies={data.strategies} leagueAccounts={data.league_accounts} analyticsReady={data.system.dashboard_trade_cache_ready !== false} researchDetails={researchDetails} controlsEnabled={connected && safetyVerified} selectedFamilyDetail={selectedFamilyDetail} onSelectFamily={selectStrategyFamily} onConfigure={changeStrategy} onRollback={undoStrategy} onConfigureFamilyResearch={configureStrategyFamilyResearch} /> : null}
        {page === 'trades' ? <TradesPage data={data} /> : null}
        {page === 'settings' ? <SettingsPage data={data} connected={connected} lastUpdateMs={lastUpdateMs} researchDetails={researchDetails} onResearchDetailsChange={changeResearchDetails} onNewRun={newRun} /> : null}
      </Suspense>
    </main>
  )
}
