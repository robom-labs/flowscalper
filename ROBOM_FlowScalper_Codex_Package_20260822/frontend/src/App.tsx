// 시장을 기본으로 다섯 개 주 메뉴와 PAPER 제어 상태를 조합하는 애플리케이션 루트다.
import { useState } from 'react'
import { Navigation } from './components/Navigation'
import { SafetyHeader } from './components/SafetyHeader'
import { useDashboard } from './hooks/useDashboard'
import { MarketPage } from './pages/MarketPage'
import { HistoryPage } from './pages/HistoryPage'
import { LeaguePositionsPage } from './pages/LeaguePositionsPage'
import { LivePage } from './pages/LivePage'
import { PerformancePage } from './pages/PerformancePage'
import { ReplayPage } from './pages/ReplayPage'
import { RiskPage } from './pages/RiskPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { StrategySymbolPage } from './pages/StrategySymbolPage'
import { SystemPage } from './pages/SystemPage'
import type { HistoryRow, PageId } from './types'

export default function App() {
  const [page, setPage] = useState<PageId>('terminal')
  const [replayTrade, setReplayTrade] = useState<HistoryRow | undefined>()
  const {
    data,
    connected,
    connectionState,
    bootstrapState,
    lastUpdateMs,
    connectionError,
    requestError,
    busyAction,
    controlOperation,
    control,
    cancelControl,
    retryControl,
    selectChart,
    configureStrategy,
    rollbackStrategy,
    clearError,
  } = useDashboard()

  const runControl = async (action: 'pause' | 'resume' | 'emergency-close' | 'new-run' | 'start-live' | 'start-demo') => {
    try {
      clearError()
      await control(action)
    } catch {
      // useDashboard가 서버의 실제 한국어 오류를 화면 상태로 보존한다.
    }
  }
  const pauseToggle = () => void runControl(data.paused ? 'resume' : 'pause')
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
    if (nextPage === 'replay') setReplayTrade(undefined)
    setPage(nextPage)
    window.scrollTo(0, 0)
  }
  const openReplay = (trade: HistoryRow) => {
    setReplayTrade(trade)
    setPage('replay')
    window.scrollTo(0, 0)
  }

  return (
    <main className="app-shell">
      <SafetyHeader data={data} connected={connected} connectionState={connectionState} lastUpdateMs={lastUpdateMs} onSummary={() => changePage('summary')} />
      <Navigation page={page} onChange={changePage} />
      {connectionError ? <p className="connection-error" role="alert">{connectionError}</p> : null}
      {bootstrapState === 'LOADING' ? <p className="bootstrap-state" role="status">프로그램 상태를 불러오는 중입니다.</p> : null}
      {bootstrapState === 'ERROR' ? <p className="connection-error" role="alert">프로그램 서버에 연결하지 못했습니다. 실행 상태를 확인하세요.</p> : null}
      {requestError ? <p className="control-error" role="alert">{requestError}</p> : null}
      {page === 'summary' ? <LivePage data={data} onNavigate={changePage} /> : null}
      {page === 'strategies' ? <StrategiesPage strategies={data.strategies} leagueAccounts={data.league_accounts} onConfigure={changeStrategy} onRollback={undoStrategy} /> : null}
      {page === 'positions' ? <LeaguePositionsPage positions={data.league_positions} strategies={data.strategies} /> : null}
      {page === 'history' ? <HistoryPage rows={data.history} currentRunId={data.status.run_id} historyScope={data.history_scope} onReplay={openReplay} /> : null}
      {page === 'replay' ? <ReplayPage trade={replayTrade} /> : null}
      {page === 'performance' ? <PerformancePage data={data} strategies={data.strategies} leagueAccounts={data.league_accounts} history={data.history} /> : null}
      {page === 'strategy-symbol' ? <StrategySymbolPage strategies={data.strategies} /> : null}
      {page === 'risk' ? <RiskPage data={data} onPauseToggle={pauseToggle} onNewRun={newRun} /> : null}
      {page === 'terminal' ? <MarketPage data={data} onChartChange={(symbol, interval) => void changeChart(symbol, interval)} onStartLive={() => void runControl('start-live')} onStartDemo={() => void runControl('start-demo')} onPauseToggle={pauseToggle} busy={busyAction !== null || Boolean(controlOperation && !['COMPLETED', 'FAILED_RETRYABLE', 'FAILED_BLOCKED', 'CANCELLED'].includes(controlOperation.state))} operation={controlOperation} onCancel={() => void cancelOperation()} onRetry={() => void retryOperation()} /> : null}
      {page === 'system' ? <SystemPage data={data} connected={connected} lastUpdateMs={lastUpdateMs} /> : null}
    </main>
  )
}
