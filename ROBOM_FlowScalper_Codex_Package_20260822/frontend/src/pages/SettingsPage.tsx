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
  onConfigureLeverage: (selectedLeverage: number, expectedRevision: number) => Promise<unknown>
}

export function SettingsPage({
  data,
  connected,
  lastUpdateMs,
  researchDetails: controlledResearchDetails,
  onResearchDetailsChange,
  onNewRun,
  onConfigureLeverage,
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
  const paperResearch = data.settings_summary?.paper_research
  const [leverageDraft, setLeverageDraft] = useState(() => ({
    revision: paperResearch?.revision ?? -1,
    selectedLeverage: paperResearch?.selected_leverage ?? 10,
  }))
  const selectedLeverage = leverageDraft.revision === (paperResearch?.revision ?? -1)
    ? leverageDraft.selectedLeverage
    : paperResearch?.selected_leverage ?? 10
  const [savingLeverage, setSavingLeverage] = useState(false)
  const saveLeverage = async () => {
    if (!paperResearch || selectedLeverage === paperResearch.selected_leverage) return
    setSavingLeverage(true)
    try {
      await onConfigureLeverage(selectedLeverage, paperResearch.revision)
    } finally {
      setSavingLeverage(false)
    }
  }
  const autostartLabel = autostart?.state === 'VERIFIED_ENABLED'
    ? '켜짐'
    : autostart?.state === 'VERIFIED_DISABLED'
      ? '꺼짐'
      : '확인되지 않음'

  return (
    <section className="settings-page" aria-labelledby="settings-heading">
      <div className="page-heading"><div><p className="section-kicker">PAPER 설정</p><h2 id="settings-heading">설정</h2><p className="heading-help">프로그램은 자동으로 계속 관찰하고, 조건이 맞으면 별도 시작 버튼 없이 PAPER 진입을 시도합니다.</p></div><span className="page-note">기본 10배</span></div>
      <section className="settings-summary-grid" aria-label="기본 설정 상태">
        <article className="panel"><span>자동 시작 · 실행 상태</span><b className={data.operation_status.paper_entry_active ? 'positive' : 'warning'}>{data.operation_status.paper_entry_active ? '자동 진입 허용' : '안전 확인 대기'}</b><small>{autostartLabel} · {autostart?.evidence_ko ?? '자동 시작 근거를 불러오는 중입니다.'}</small></article>
        <article className="panel"><span>연결</span><b>{connected ? '화면 연결됨' : '다시 연결 중'}</b></article>
        <article className="panel"><span>저장공간</span><b className={storageAllowed ? 'positive' : 'warning'}>{storageAllowed ? '신규 진입 허용' : '안전 잠금'}</b></article>
        <article className="panel"><span>연속 PAPER 연구</span><b>일·주 한도 없음</b><small>거래 횟수·기간손실·연속손실 쿨다운으로 멈추지 않음</small></article>
        <article className="panel"><label className="settings-toggle"><span>연구 상세 표시</span><input type="checkbox" checked={researchDetails} onChange={(event) => setResearchPreference(event.target.checked)} /></label><small>화면 상세만 바뀌며 전략 실행에는 영향 없음</small></article>
      </section>
      <section className="panel leverage-settings" aria-labelledby="leverage-setting-heading">
        <div><p className="section-kicker">모든 현재 진입 가능 전략 공통</p><h3 id="leverage-setting-heading">PAPER 레버리지</h3><p>기본 10배이며 최대 100배까지 선택할 수 있습니다. 이미 열린 거래는 진입 당시 배수를 유지하고 새 진입부터 적용됩니다.</p></div>
        <label><span>진입 배수</span><select aria-label="PAPER 레버리지" value={selectedLeverage} disabled={!paperResearch || savingLeverage} onChange={(event) => setLeverageDraft({ revision: paperResearch?.revision ?? -1, selectedLeverage: Number(event.target.value) })}>{(paperResearch?.allowed_leverages ?? [1, 2, 3, 5, 10, 20, 25, 50, 75, 100]).map((leverage) => <option key={leverage} value={leverage}>{leverage}배</option>)}</select></label>
        <button type="button" disabled={!paperResearch || savingLeverage || selectedLeverage === paperResearch.selected_leverage} onClick={() => void saveLeverage()}>{savingLeverage ? '적용 중…' : '선택 배수 적용'}</button>
        <small>수수료와 손익은 실제 명목금액 기준이며, PAPER 증거금은 명목금액 ÷ 선택 배수입니다. 거래당 계획손실과 호가 깊이 검증은 그대로 유지됩니다.</small>
      </section>
      <SafetySettings data={data} onNewRun={onNewRun} />
      <details className="panel advanced-details settings-diagnostics" open={researchDetails || undefined}>
        <summary>전문가 진단</summary>
        <SystemDiagnostics data={data} connected={connected} lastUpdateMs={lastUpdateMs} />
      </details>
    </section>
  )
}
