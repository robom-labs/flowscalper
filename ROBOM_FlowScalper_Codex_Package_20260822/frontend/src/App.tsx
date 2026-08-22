// 여섯 PAPER 사용자 화면과 안전 제어를 조합하는 애플리케이션 루트다.
import { useState } from 'react'
import { Navigation } from './components/Navigation'
import { SafetyHeader } from './components/SafetyHeader'
import { useDashboard } from './hooks/useDashboard'
import { HistoryPage } from './pages/HistoryPage'
import { LivePage } from './pages/LivePage'
import { PerformancePage } from './pages/PerformancePage'
import { ReplayPage } from './pages/ReplayPage'
import { RiskPage } from './pages/RiskPage'
import { SystemPage } from './pages/SystemPage'
import type { PageId } from './types'

export default function App() {
  const [page, setPage] = useState<PageId>('live')
  const [controlError, setControlError] = useState('')
  const { data, connected, control } = useDashboard()

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

  return (
    <main>
      <SafetyHeader data={data} connected={connected} />
      <Navigation page={page} onChange={setPage} />
      {controlError ? <p className="control-error" role="alert">{controlError}</p> : null}
      {page === 'live' ? <LivePage data={data} onPauseToggle={pauseToggle} onClose={emergencyClose} onStartLive={() => void runControl('start-live')} onStartDemo={() => void runControl('start-demo')} /> : null}
      {page === 'history' ? <HistoryPage rows={data.history} onReplay={() => setPage('replay')} /> : null}
      {page === 'replay' ? <ReplayPage chart={data.chart} trade={data.history[0]} /> : null}
      {page === 'performance' ? <PerformancePage performance={data.performance} /> : null}
      {page === 'risk' ? <RiskPage data={data} onPauseToggle={pauseToggle} onNewRun={newRun} /> : null}
      {page === 'system' ? <SystemPage data={data} /> : null}
    </main>
  )
}
