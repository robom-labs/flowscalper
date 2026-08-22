// 일곱 PAPER 사용자 화면과 실제 백엔드 제어를 조합하는 애플리케이션 루트다.
import { useState } from 'react'
import { Navigation } from './components/Navigation'
import { SafetyHeader } from './components/SafetyHeader'
import { useDashboard } from './hooks/useDashboard'
import { HistoryPage } from './pages/HistoryPage'
import { LivePage } from './pages/LivePage'
import { PerformancePage } from './pages/PerformancePage'
import { ReplayPage } from './pages/ReplayPage'
import { RiskPage } from './pages/RiskPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { SystemPage } from './pages/SystemPage'
import type { HistoryRow, PageId } from './types'

export default function App() {
  const [page, setPage] = useState<PageId>('live')
  const [controlError, setControlError] = useState('')
  const [replayTrade, setReplayTrade] = useState<HistoryRow | undefined>()
  const {
    data,
    connected,
    connectionState,
    lastUpdateMs,
    control,
    selectChart,
    configureStrategy,
  } = useDashboard()

  const runControl = async (
    action: 'pause' | 'resume' | 'emergency-close' | 'new-run' | 'start-live' | 'start-demo',
  ) => {
    try {
      setControlError('')
      await control(action)
    } catch {
      setControlError('PAPER 제어 요청이 실패했습니다. 시스템 연결을 확인하세요.')
    }
  }

  const pauseToggle = () => void runControl(data.paused ? 'resume' : 'pause')
  const emergencyClose = () => {
    if (window.confirm('현재 PAPER 포지션만 시뮬레이션 종료할까요?')) {
      void runControl('emergency-close')
    }
  }
  const newRun = () => {
    if (window.confirm('기존 Run 기록을 보존하고 새 PAPER Run을 만들까요?')) {
      void runControl('new-run')
    }
  }

  const changeChart = async (symbol: string, intervalSeconds: number) => {
    try {
      setControlError('')
      await selectChart(symbol, intervalSeconds)
    } catch {
      setControlError('차트 종목·시간구간을 적용하지 못했습니다.')
    }
  }

  const changeStrategy = async (
    strategyId: string,
    configuration: {
      mode: 'ACTIVE' | 'SHADOW' | 'OFF'
      long_enabled: boolean
      short_enabled: boolean
    },
  ) => {
    try {
      setControlError('')
      return await configureStrategy(strategyId, configuration)
    } catch (error) {
      setControlError('전략 설정을 저장하지 못했습니다. 연결을 확인하세요.')
      throw error
    }
  }

  const changePage = (nextPage: PageId) => {
    setPage(nextPage)
    window.scrollTo(0, 0)
  }

  const openReplay = (trade: HistoryRow) => {
    setReplayTrade(trade)
    changePage('replay')
  }

  return (
    <main>
      <SafetyHeader data={data} connected={connected} connectionState={connectionState} />
      <Navigation page={page} onChange={changePage} />
      {controlError ? <p className="control-error" role="alert">{controlError}</p> : null}
      {page === 'live' ? <LivePage data={data} onPauseToggle={pauseToggle} onClose={emergencyClose} onStartLive={() => void runControl('start-live')} onStartDemo={() => void runControl('start-demo')} onChartChange={(symbol, interval) => void changeChart(symbol, interval)} /> : null}
      {page === 'strategies' ? <StrategiesPage strategies={data.strategies} shadowAccounts={data.shadow_accounts} onConfigure={changeStrategy} /> : null}
      {page === 'history' ? <HistoryPage rows={data.history} onReplay={openReplay} /> : null}
      {page === 'replay' ? <ReplayPage trade={replayTrade} /> : null}
      {page === 'performance' ? <PerformancePage performance={data.performance} strategies={data.strategies} shadowAccounts={data.shadow_accounts} history={data.history} /> : null}
      {page === 'risk' ? <RiskPage data={data} onPauseToggle={pauseToggle} onNewRun={newRun} /> : null}
      {page === 'system' ? <SystemPage data={data} connected={connected} lastUpdateMs={lastUpdateMs} /> : null}
    </main>
  )
}
