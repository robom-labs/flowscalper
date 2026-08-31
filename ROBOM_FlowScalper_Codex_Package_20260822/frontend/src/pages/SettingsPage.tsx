// 안전 제어와 저장·연결 상태를 먼저 보여주고 원시 진단은 접어 두는 V6 설정 화면이다.
import { useState } from 'react'
import { SafetySettings } from '../components/SafetySettings'
import { SystemDiagnostics } from '../components/SystemDiagnostics'
import type { DashboardData } from '../types'

const researchPreferenceKey = 'robom.flowscalper.research-details'

type Props = {
  data: DashboardData
  connected: boolean
  lastUpdateMs: number | null
  researchDetails?: boolean
  onResearchDetailsChange?: (enabled: boolean) => void
  onNewRun: () => void
}

export function SettingsPage({
  data,
  connected,
  lastUpdateMs,
  researchDetails: controlledResearchDetails,
  onResearchDetailsChange,
  onNewRun,
}: Props) {
  const [localResearchDetails, setLocalResearchDetails] = useState(() => {
    try { return globalThis.localStorage?.getItem(researchPreferenceKey) === '1' } catch { return false }
  })
  const researchDetails = controlledResearchDetails ?? localResearchDetails
  const setResearchPreference = (enabled: boolean) => {
    setLocalResearchDetails(enabled)
    onResearchDetailsChange?.(enabled)
    try { globalThis.localStorage?.setItem(researchPreferenceKey, enabled ? '1' : '0') } catch { /* 로컬 저장이 막혀도 실행 설정에는 영향이 없다. */ }
  }
  const storageAllowed = data.system.storage_entry_allowed !== false
  const autostart = data.settings_summary?.autostart
  const autostartLabel = autostart?.state === 'VERIFIED_ENABLED'
    ? '켜짐'
    : autostart?.state === 'VERIFIED_DISABLED'
      ? '꺼짐'
      : '확인되지 않음'

  return (
    <section className="settings-page" aria-labelledby="settings-heading">
      <div className="page-heading"><div><p className="section-kicker">PAPER 설정</p><h2 id="settings-heading">설정</h2><p className="heading-help">새 진입 제어와 저장·연결 상태를 확인합니다. 비용 가정 변경은 새 모델·Run 분리 전에는 지원하지 않습니다.</p></div><span className="page-note">기본 설정</span></div>
      <section className="settings-summary-grid" aria-label="기본 설정 상태">
        <article className="panel"><span>자동 시작 · 읽기 전용</span><b className={autostart?.state === 'NOT_PROVEN' ? 'warning' : ''}>{autostartLabel}</b><small>{autostart?.evidence_ko ?? 'Backend 자동 시작 근거를 불러오는 중입니다.'}</small></article>
        <article className="panel"><span>연결</span><b>{connected ? '화면 연결됨' : '다시 연결 중'}</b></article>
        <article className="panel"><span>저장공간</span><b className={storageAllowed ? 'positive' : 'warning'}>{storageAllowed ? '신규 진입 허용' : '안전 잠금'}</b></article>
        <article className="panel"><span>비용 가정</span><b>BASE · STRESS 분리</b><small>현재 화면에서는 수정하지 않음</small></article>
        <article className="panel"><label className="settings-toggle"><span>연구 상세 표시</span><input type="checkbox" checked={researchDetails} onChange={(event) => setResearchPreference(event.target.checked)} /></label><small>화면 상세만 바뀌며 전략 실행에는 영향 없음</small></article>
      </section>
      <SafetySettings data={data} onNewRun={onNewRun} />
      <details className="panel advanced-details settings-diagnostics" open={researchDetails || undefined}>
        <summary>전문가 진단</summary>
        <SystemDiagnostics data={data} connected={connected} lastUpdateMs={lastUpdateMs} />
      </details>
    </section>
  )
}
