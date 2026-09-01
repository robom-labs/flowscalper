// 전략의 핵심 상태는 짧은 표로, BASE·STRESS 세부 성과는 drawer로 분리한다.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, fetchJson } from '../api/client'
import { SideDrawer } from '../components/SideDrawer'
import { StrategyPerformancePanel } from '../components/StrategyPerformancePanel'
import { StrategySymbolPanel } from '../components/StrategySymbolPanel'
import { costProfileLabel, formatDurationMs, formatPercentFraction, formatRatio, formatUsdt } from '../format'
import { modeLabels, strategyWaitReasonLabel } from '../strategyPresentation'
import type {
  DashboardData,
  HistoryRow,
  LeagueAccount,
  OrderflowFilterStatus,
  ResearchSourceMetadata,
  StrategyFamilyCondition,
  StrategyFamilyCatalogRow,
  StrategyFamilyConditionsResponse,
  StrategyFamilyDetail,
  StrategyPerformance,
  StrategyRow,
  StrategySummaryRow,
  V9ResearchCandidate,
} from '../types'

type StrategyConfiguration = {
  mode: 'ACTIVE' | 'SHADOW' | 'OFF'
  long_enabled: boolean
  short_enabled: boolean
  expected_revision: number
}

type FamilyResearchConfiguration = {
  research_enabled: boolean
  expected_revision: number
  reason: string
}

type Props = {
  strategies: StrategySummaryRow[]
  leagueAccounts: LeagueAccount[]
  data?: DashboardData
  history?: HistoryRow[]
  analyticsReady?: boolean
  researchDetails?: boolean
  selectedFamilyDetail?: StrategyFamilyDetail | null
  controlsEnabled: boolean
  onSelectFamily?: (familyId: string | null) => void
  onConfigure: (strategyId: string, configuration: StrategyConfiguration) => Promise<unknown>
  onRollback?: (strategyId: string, targetRevision: number, expectedRevision: number) => Promise<unknown>
  onConfigureFamilyResearch?: (
    familyId: string,
    configuration: FamilyResearchConfiguration,
  ) => Promise<StrategyFamilyDetail>
}

type CostProfile = 'BASE' | 'STRESS'
type StrategyDetailTab = 'status' | 'conditions' | 'exit' | 'performance' | 'sources' | 'previous'
type StrategySortKey = 'strategy' | 'status' | 'wilson' | 'sampleSize' | 'expectancy' | 'net' | 'openPositions'
type SortDirection = 'ascending' | 'descending'
type FamilyCategoryFilter = 'all' | 'trend' | 'breakout' | 'reversal' | 'filter' | 'marketNeutral' | 'ranking'
type StrategyFamilyDetailResult = {
  familyId: string
  detail: StrategyFamilyDetail | null
  error: string
}

type FamilyConditionsLoad = {
  familyId: string
  state: 'loading' | 'ready' | 'error'
  data: StrategyFamilyConditionsResponse | null
  error: string
  refreshError: string
  refreshing: boolean
  lastUpdatedMs: number | null
}

type FamilyResearchUndo = {
  familyId: string
  researchEnabled: boolean
  expectedRevision: number
}

const orderflowFamilyId = 'ORDERFLOW_CONFIRMATION'

const v9RoleLabels: Record<V9ResearchCandidate['role'], string> = {
  ENTRY: '방향 전략',
  MARKET_NEUTRAL_MULTI_LEG: '시장중립 전략',
  ROUTER: '라우터',
  RISK_OVERLAY: '위험 축소',
  FILTER: '필터',
  STATISTICS: '통계 검증',
  SELECTION: '후보 선별',
}

const v9ReadinessLabels: Record<V9ResearchCandidate['readiness'], string> = {
  SOURCE_IMPLEMENTED_NOT_CONNECTED: '소스 구현 · 진입 미연결',
  PARTIAL_SOURCE_NOT_CONNECTED: '핵심 소스 일부 구현 · 진입 미연결',
  BLOCKED_PREREQUISITE: '선행 검증 대기',
  BLOCKED_ENGINE: '실행 엔진 대기',
}

const strategyDetailTabs: Array<{ id: StrategyDetailTab; label: string }> = [
  { id: 'status', label: '지금 상태' },
  { id: 'conditions', label: '진입조건' },
  { id: 'exit', label: '청산' },
  { id: 'performance', label: '성과' },
  { id: 'sources', label: '출처' },
  { id: 'previous', label: '이전 버전' },
]

const familyCategoryOptions: Array<{ id: FamilyCategoryFilter; label: string }> = [
  { id: 'all', label: '전체' },
  { id: 'trend', label: '추세' },
  { id: 'breakout', label: '돌파' },
  { id: 'reversal', label: '반전' },
  { id: 'filter', label: '필터' },
  { id: 'marketNeutral', label: '시장중립' },
  { id: 'ranking', label: '순위' },
]

function profileUniqueSamples(
  report: Pick<StrategyPerformance, 'sample_size' | 'profile_unique_opportunity_count'>,
) {
  return report.profile_unique_opportunity_count ?? report.sample_size
}

function inFamilyCategory(strategy: StrategySummaryRow, filter: FamilyCategoryFilter, profile: CostProfile) {
  if (filter === 'all') return true
  if (filter === 'ranking') {
    const report = strategy.performance[profile]
    return profileUniqueSamples(report) >= 30
      && strategy.final_ranking_eligible === true
  }
  if (filter === 'trend') return strategy.family_id === 'TREND_PULLBACK'
  if (filter === 'breakout') return strategy.family_id === 'BREAKOUT_RUNNER'
  if (filter === 'reversal') return strategy.family_id === 'EXHAUSTION_REVERSION'
  if (filter === 'marketNeutral') {
    return strategy.family_id === 'MARKET_NEUTRAL' || strategy.role === 'MARKET_NEUTRAL_MULTI_LEG'
  }
  return strategy.family_id === orderflowFamilyId
    || strategy.family_id === 'POSITIONING_LIQUIDATION'
    || strategy.family_id === 'MARKET_REGIME_FILTERS'
    || strategy.family_id === 'SESSION_PROFILE'
    || strategy.role === 'FILTER'
    || strategy.role === 'ROUTER'
}

function requestErrorMessage(error: unknown) {
  return error instanceof ApiError
    ? error.messageKo
    : '전략 조건을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.'
}

function isFamilyConditionsResponse(value: unknown, familyId: string): value is StrategyFamilyConditionsResponse {
  if (!value || typeof value !== 'object') return false
  const response = value as Partial<StrategyFamilyConditionsResponse>
  return response.family_id === familyId
    && typeof response.total === 'number'
    && (typeof response.passed === 'number' || response.passed === null)
    && Array.isArray(response.top_blockers)
    && Array.isArray(response.conditions)
}

function useFamilyConditions(familyId: string) {
  const [reloadRevision, setReloadRevision] = useState(0)
  const [result, setResult] = useState<FamilyConditionsLoad | null>(null)
  useEffect(() => {
    let disposed = false
    let inFlight = false
    let controller: AbortController | null = null
    const load = () => {
      if (inFlight) return
      inFlight = true
      controller = new AbortController()
      setResult((current) => current?.familyId === familyId && current.state === 'ready'
        ? { ...current, refreshing: true, refreshError: '' }
        : { familyId, state: 'loading', data: null, error: '', refreshError: '', refreshing: true, lastUpdatedMs: null })
      void fetchJson<StrategyFamilyConditionsResponse>(
        `/api/strategy-families/${encodeURIComponent(familyId)}/conditions`,
        { signal: controller.signal },
      ).then((data) => {
        if (disposed) return
        if (!isFamilyConditionsResponse(data, familyId)) throw new Error('invalid family conditions response')
        setResult({ familyId, state: 'ready', data, error: '', refreshError: '', refreshing: false, lastUpdatedMs: Date.now() })
      }).catch((error: unknown) => {
        if (disposed || controller?.signal.aborted) return
        const message = requestErrorMessage(error)
        setResult((current) => current?.familyId === familyId && current.data
          ? { ...current, state: 'ready', refreshError: message, refreshing: false }
          : { familyId, state: 'error', data: null, error: message, refreshError: '', refreshing: false, lastUpdatedMs: null })
      }).finally(() => {
        inFlight = false
      })
    }
    load()
    const timer = window.setInterval(load, 5_000)
    return () => {
      disposed = true
      controller?.abort()
      window.clearInterval(timer)
    }
  }, [familyId, reloadRevision])
  const retry = useCallback(() => {
    setReloadRevision((current) => current + 1)
  }, [])
  return {
    result: result?.familyId === familyId ? result : null,
    retry,
  }
}

const setupStateLabels: Record<string, string> = {
  WAITING_DATA: '시장 데이터 준비 중',
  REJECTED: '진입 조건 대기',
  BLOCKED: '진입 조건 대기',
  QUALIFIED: '진입 조건 충족',
  PASSED: '진입 조건 충족',
  ALLOWED: '진입 조건 충족',
  OPEN: 'PAPER 포지션 관리 중',
  PENDING: 'PAPER 진입 확인 중',
  FILTER_OFF: '연구 필터 꺼짐',
  RESEARCH_OFF: 'PAPER 연구 꺼짐',
  RESEARCH_NOT_IMPLEMENTED: '조건 측정 준비 전',
}

const conditionStatusLabels: Record<string, { label: string; tone: string }> = {
  PASSED: { label: '충족', tone: 'passed' },
  TRUE: { label: '충족', tone: 'passed' },
  ALLOWED: { label: '충족', tone: 'passed' },
  FAILED: { label: '미충족', tone: 'failed' },
  REJECTED: { label: '미충족', tone: 'failed' },
  BLOCKED: { label: '미충족', tone: 'failed' },
  WAITING: { label: '확인 중', tone: 'waiting' },
  PENDING: { label: '확인 중', tone: 'waiting' },
  NOT_AVAILABLE: { label: '실측 없음', tone: 'unavailable' },
}

function setupStateLabel(state: string | undefined) {
  return state ? setupStateLabels[state] ?? '측정 상태 확인 중' : '측정 상태 확인 중'
}

function conditionStatus(status: string) {
  return conditionStatusLabels[status] ?? { label: '확인 중', tone: 'waiting' }
}

function includesKorean(value: string) {
  return /[가-힣]/.test(value)
}

function looksLikeTechnicalHash(value: string) {
  return /^(?:sha\d*[:_-]?|0x)[a-f\d]{16,}$/i.test(value)
    || /^[a-f\d]{32,}$/i.test(value)
}

function conditionCurrentValue(condition: StrategyFamilyCondition) {
  const value = condition.current_value
  if (value === null || value === '') return '측정 대기'
  if (typeof value === 'boolean') return value ? '예' : '아니요'
  const rendered = String(value)
  if (/해시|hash/i.test(`${condition.condition_id} ${condition.label_ko}`) || looksLikeTechnicalHash(rendered)) {
    return '기술 값 숨김'
  }
  return rendered
}

function humanConditionBlockers(data: StrategyFamilyConditionsResponse) {
  const conditionReasons = data.conditions
    .filter((condition) => conditionStatus(condition.status).tone !== 'passed')
    .map((condition) => condition.reason_ko?.trim() ?? '')
    .filter((reason) => includesKorean(reason))
  const responseReasons = data.top_blockers
    .map((reason) => reason.trim())
    .filter((reason) => includesKorean(reason) && !looksLikeTechnicalHash(reason))
  return [...new Set([...conditionReasons, ...responseReasons])].slice(0, 3)
}

function executionValue(value: string | number | null | undefined) {
  return value === null || value === undefined || value === '' ? '계획 확인 전' : String(value)
}

function executionExpiry(data: StrategyFamilyConditionsResponse) {
  const value = data.execution?.expiry ?? data.execution?.expires_at ?? data.execution?.expiry_ts_ms
  if (typeof value === 'number' && value > 0) return new Date(value).toLocaleString('ko-KR')
  return executionValue(value)
}

function fallbackResearchSources(sourceIds: string[]): ResearchSourceMetadata[] {
  return sourceIds.map((sourceId) => ({
    source_id: sourceId,
    title: sourceId,
    publisher: '등록 정보 없음',
    date: 'NOT_PROVEN',
    url: null,
    idea_used: '구조화된 출처 설명을 확인하지 못했습니다.',
    our_modification: '메타데이터 확인 전에는 연구 근거로 단정하지 않습니다.',
    metadata_status: 'NOT_PROVEN',
  }))
}

function ResearchSources({ sources, sourceIds }: { sources: ResearchSourceMetadata[] | undefined; sourceIds: string[] }) {
  const rows = sources?.length ? sources : fallbackResearchSources(sourceIds)
  return <section className="profile-detail-block strategy-sources">
    <h3>출처</h3>
    {rows.length ? <div className="strategy-source-list">{rows.map((source) => <article key={source.source_id}>
      <div><strong>{source.title}</strong><span className={source.metadata_status === 'NOT_PROVEN' ? 'warning' : ''}>{source.publisher} · {source.date}</span></div>
      {source.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">원문 새 창에서 열기</a> : <span>URL · NOT_PROVEN</span>}
      <dl><div><dt>사용한 아이디어</dt><dd>{source.idea_used}</dd></div><div><dt>우리 수정</dt><dd>{source.our_modification}</dd></div></dl>
    </article>)}</div> : <p>등록된 출처가 없습니다.</p>}
  </section>
}

type StrategyConditionsView = 'status' | 'conditions' | 'exit'

function StrategyConditionsPanel({
  familyId,
  view,
}: {
  familyId: string
  view: StrategyConditionsView
}) {
  const { result, retry } = useFamilyConditions(familyId)
  const data = result?.state === 'ready' ? result.data : null
  const blockers = data ? humanConditionBlockers(data) : []
  const heading = view === 'status'
    ? '지금 상태'
    : view === 'conditions'
      ? '진입 조건 실측'
      : '청산 계획'
  return (
    <section className="profile-detail-block strategy-conditions" aria-labelledby="strategy-conditions-heading">
      <h3 id="strategy-conditions-heading">{heading}</h3>
      <p className="profile-scope-note">공개시장 PAPER 판단값이며 5초마다 선택한 family의 최신 상태를 다시 확인합니다.</p>
      {!result || result.state === 'loading' ? <p role="status">조건과 현재값을 불러오는 중입니다.</p> : null}
      {result?.state === 'error' ? <div className="strategy-condition-error" role="alert"><p>{result.error}</p><button type="button" className="secondary-button" onClick={retry}>조건 다시 불러오기</button></div> : null}
      {data ? <>
        <p className="profile-scope-note" role="status">{result?.refreshing
          ? '최신 조건값을 확인하는 중입니다.'
          : result?.lastUpdatedMs
            ? `5초마다 자동 확인 · 마지막 ${evaluationTime(result.lastUpdatedMs)}`
            : '최신 조건값 확인 전입니다.'}</p>
        {result?.refreshError ? <div className="strategy-condition-error" role="alert"><p>{result.refreshError}</p><button type="button" className="secondary-button" onClick={retry}>조건 다시 불러오기</button></div> : null}
        {view === 'status' ? <dl className="strategy-condition-summary">
          <div><dt>준비 상태</dt><dd>{setupStateLabel(data.setup_state)}</dd></div>
          <div><dt>충족 조건</dt><dd>{data.passed === null ? '측정 전' : `${data.passed}/${data.total}`}</dd></div>
          <div><dt>확인 종목</dt><dd>{data.symbol || '선택 종목 측정 대기'}</dd></div>
          <div><dt>진입 대기</dt><dd>{typeof data.pending_count === 'number' ? `${data.pending_count}건` : '정보 없음'}</dd></div>
          <div><dt>진행 포지션</dt><dd>{typeof data.open_count === 'number' ? `${data.open_count}건` : '정보 없음'}</dd></div>
          <div><dt>만료</dt><dd>{executionExpiry(data)}</dd></div>
        </dl> : null}
        {view === 'status' && blockers.length ? <div className="strategy-blockers"><strong>먼저 확인할 조건</strong><ul>{blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div> : view === 'status' && data.top_blockers.length ? <p className="profile-scope-note">세부 미충족 사유는 진입조건 탭에서 확인할 수 있습니다.</p> : null}
        {view === 'conditions' && data.conditions.length ? <div className="table-scroll"><table className="strategy-condition-table" aria-label="선택 전략의 진입 조건 실측"><thead><tr><th>조건</th><th>기준</th><th>현재값</th><th>상태</th></tr></thead><tbody>{data.conditions.map((condition, index) => {
          const state = conditionStatus(condition.status)
          return <tr key={condition.condition_id || index}>
            <td data-label="조건">{includesKorean(condition.label_ko) ? condition.label_ko : `조건 ${index + 1}`}</td>
            <td data-label="기준">{looksLikeTechnicalHash(condition.threshold_ko) ? '기술 값 숨김' : condition.threshold_ko || '기준 정보 준비 중'}</td>
            <td data-label="현재값">{conditionCurrentValue(condition)}</td>
            <td data-label="상태"><span className={`condition-state ${state.tone}`}>{state.label}</span>{condition.reason_ko && includesKorean(condition.reason_ko) ? <small>{condition.reason_ko}</small> : null}</td>
          </tr>
        })}</tbody></table></div> : view === 'conditions' ? <p className="strategy-condition-empty">조건별 측정값이 아직 없습니다. 전략 설명은 볼 수 있지만 진입 여부를 단정하지 않습니다.</p> : null}
        {view === 'exit' ? <section className="strategy-execution-plan" aria-label="선택 전략의 청산 정보">
          <h4>청산 정보</h4>
          <dl className="drawer-detail-list">
            <div><dt>entry</dt><dd>{executionValue(data.execution?.entry)}</dd></div>
            <div><dt>initial stop</dt><dd>{executionValue(data.execution?.initial_stop)}</dd></div>
            <div><dt>TP1</dt><dd>{executionValue(data.execution?.TP1 ?? data.execution?.take_profit_1)}</dd></div>
            <div><dt>TP2</dt><dd>{executionValue(data.execution?.TP2 ?? data.execution?.take_profit_2)}</dd></div>
            <div><dt>trailing activation</dt><dd>{executionValue(data.execution?.trailing_activation)}</dd></div>
            <div><dt>current trail</dt><dd>{executionValue(data.execution?.current_trail)}</dd></div>
            <div><dt>remaining quantity</dt><dd>{executionValue(data.execution?.remaining_quantity)}</dd></div>
          </dl>
        </section> : null}
      </> : null}
    </section>
  )
}

const upliftStatusLabels: Record<string, string> = {
  NOT_PROVEN_NO_PAIRED_FILTER_SAMPLE: 'ON/OFF 비교 표본을 모으는 중',
  NOT_PROVEN: '효과 검증 표본을 모으는 중',
  PROVEN: '비교 검증 완료',
  DEGRADED: '효과 재확인 필요',
}

const dataHealthLabels: Record<string, string> = {
  HEALTHY: '정상',
  UNHEALTHY: '확인 필요',
  DEGRADED: '일부 지연',
  WAITING_DATA: '측정 대기',
}

function orderflowScore(filter: OrderflowFilterStatus) {
  const latest = filter.latest?.[0]
  const value = filter.latest_score ?? latest?.score
  if (value === null || value === undefined || value === '') return '측정 대기'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed.toFixed(2)} / 1.00` : '측정 대기'
}

function orderflowDataHealth(filter: OrderflowFilterStatus) {
  const status = filter.data_health
    ?? (filter.latest?.some((row) => row.data_health === 'UNHEALTHY') ? 'UNHEALTHY' : filter.latest?.[0]?.data_health)
  return status ? dataHealthLabels[status] ?? '상태 확인 중' : '측정 대기'
}

function affectedStrategyLabels(filter: OrderflowFilterStatus, strategies: StrategySummaryRow[]) {
  const identifiers = filter.affected_strategy_ids ?? []
  const known = identifiers.flatMap((strategyId) => {
    const strategy = strategies.find((row) => row.strategy_id === strategyId)
    return strategy ? [strategy.family_label_ko || strategy.short_name || strategy.display_name_ko] : []
  })
  if (known.length) return [...new Set(known)].join(' · ')
  return identifiers.length ? `현재 전략 ${identifiers.length}개` : '연결 전략 정보 없음'
}

function summaryStrategyLabel(strategy: StrategySummaryRow | undefined, strategyId: string) {
  if (!strategy || strategy.strategy_id !== strategyId) return '알 수 없는 이전 전략'
  const family = strategy.family_label_ko?.trim() || strategy.short_name
  const variant = strategy.variant_label_ko?.trim() || strategy.display_name_ko
  return family === variant ? family : `${family} · ${variant}`
}

function OrderflowFilterPanel({
  strategies,
  controlsEnabled,
  onConfigureFamilyResearch,
}: {
  strategies: StrategySummaryRow[]
  controlsEnabled: boolean
  onConfigureFamilyResearch?: Props['onConfigureFamilyResearch']
}) {
  const { result, retry } = useFamilyConditions(orderflowFamilyId)
  const [saving, setSaving] = useState(false)
  const [mutationMessage, setMutationMessage] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [undo, setUndo] = useState<FamilyResearchUndo | null>(null)
  const filter = result?.state === 'ready' ? result.data?.filter ?? null : null
  const enabled = filter?.research_enabled ?? filter?.enabled
  const revision = filter?.revision
  const applyResearchEnabled = useCallback(async (
    nextEnabled: boolean,
    expectedRevision: number,
    reason: string,
    undoEnabled?: boolean,
  ) => {
    setSaving(true)
    setMutationMessage('')
    setMutationError('')
    try {
      if (!controlsEnabled || !onConfigureFamilyResearch) {
        throw new ApiError({
          code: 'PAPER_SAFETY_NOT_VERIFIED',
          messageKo: 'PAPER 안전 상태를 확인할 때까지 전략 변경을 잠급니다.',
        })
      }
      const detail = await onConfigureFamilyResearch(
        orderflowFamilyId,
        {
          research_enabled: nextEnabled,
          expected_revision: expectedRevision,
          reason,
        },
      )
      const current = detail.variants.find((variant) => variant.is_current_variant)?.setting
      const responseRevision = current?.settings_revision ?? current?.revision
      if (typeof responseRevision !== 'number') throw new Error('missing family revision')
      setUndo(undoEnabled === undefined ? null : {
        familyId: orderflowFamilyId,
        researchEnabled: undoEnabled,
        expectedRevision: responseRevision,
      })
      setMutationMessage(`주문흐름 확인 필터를 ${nextEnabled ? '켰습니다' : '껐습니다'}. PAPER 연구 판단에만 반영됩니다.`)
      retry()
    } catch (error) {
      setMutationError(error instanceof ApiError ? error.messageKo : '필터 설정을 변경하지 못했습니다. 최신 상태를 다시 확인해 주세요.')
      if (error instanceof ApiError && error.status === 409) retry()
    } finally {
      setSaving(false)
    }
  }, [controlsEnabled, onConfigureFamilyResearch, retry])
  const toggle = useCallback(async () => {
    if (!filter || typeof enabled !== 'boolean' || typeof revision !== 'number') return
    const nextEnabled = !enabled
    if (!window.confirm(`주문흐름 확인 필터를 ${nextEnabled ? '켜' : '꺼'}서 PAPER 진입 판단에 반영할까요?`)) return
    await applyResearchEnabled(
      nextEnabled,
      revision,
      'USER_STRATEGY_CENTER_ORDERFLOW_FILTER',
      enabled,
    )
  }, [applyResearchEnabled, enabled, filter, revision])
  const undoMutation = useCallback(async () => {
    if (!undo) return
    await applyResearchEnabled(
      undo.researchEnabled,
      undo.expectedRevision,
      'USER_STRATEGY_CENTER_ORDERFLOW_FILTER_UNDO',
    )
  }, [applyResearchEnabled, undo])
  return (
    <section className="panel orderflow-filter-panel" aria-labelledby="orderflow-filter-heading" aria-busy={!result || result.state === 'loading'}>
      <div className="orderflow-filter-heading">
        <div><p className="section-kicker">진입 품질 보조 필터</p><h3 id="orderflow-filter-heading">주문흐름 확인 필터</h3><p>PAPER 후보의 체결·호가 흐름을 한 번 더 확인하며 자체 진입 신호는 만들지 않습니다.</p></div>
        {filter && typeof enabled === 'boolean' ? <div className="orderflow-filter-action"><span className={enabled ? 'orderflow-state on' : 'orderflow-state off'}>{enabled ? 'ON' : 'OFF'}</span><button type="button" className="secondary-button" aria-label={`주문흐름 확인 필터 ${enabled ? '끄기' : '켜기'}`} aria-pressed={enabled} disabled={!controlsEnabled || !onConfigureFamilyResearch || saving || typeof revision !== 'number'} onClick={() => void toggle()}>{saving ? '저장 중' : enabled ? '연구 필터 끄기' : '연구 필터 켜기'}</button></div> : null}
      </div>
      {!result || result.state === 'loading' ? <p className="orderflow-filter-state">주문흐름 필터 상태를 불러오는 중입니다.</p> : null}
      {result?.state === 'error' ? <div className="strategy-condition-error" role="alert"><p>{result.error}</p><button type="button" className="secondary-button" onClick={retry}>필터 상태 다시 불러오기</button></div> : null}
      {result?.state === 'ready' && !filter ? <div className="orderflow-filter-state"><b>주문흐름 필터 상태가 아직 없습니다.</b><span>필터 runtime이 준비되기 전에는 ON/OFF로 표시하거나 진입 효과를 단정하지 않습니다.</span></div> : null}
      {filter ? <dl className="orderflow-filter-grid">
        <div><dt>최신 score</dt><dd>{orderflowScore(filter)}</dd></div>
        <div><dt>영향 전략</dt><dd>{affectedStrategyLabels(filter, strategies)}</dd></div>
        <div><dt>효과 검증</dt><dd>{filter.uplift_status ? upliftStatusLabels[filter.uplift_status] ?? '효과 검증 상태 확인 중' : '비교 표본 대기'}</dd></div>
        <div><dt>데이터 상태</dt><dd>{orderflowDataHealth(filter)}</dd></div>
      </dl> : null}
      <p className="orderflow-safety-note">자체 CandidatePlan·계좌·거래를 만들지 않는 PAPER 확인 필터입니다.</p>
      {mutationMessage ? <div className="orderflow-mutation-message" role="status"><span>{mutationMessage}</span>{undo ? <button type="button" className="secondary-button" disabled={!controlsEnabled || !onConfigureFamilyResearch || saving} onClick={() => void undoMutation()}>실행 취소</button> : null}</div> : null}
      {mutationError ? <p className="strategy-condition-error" role="alert">{mutationError}</p> : null}
    </section>
  )
}

const defaultSortDirection: Record<StrategySortKey, SortDirection> = {
  strategy: 'ascending',
  status: 'ascending',
  wilson: 'descending',
  sampleSize: 'descending',
  expectancy: 'descending',
  net: 'descending',
  openPositions: 'descending',
}

const mobileSortOptions: Array<{ key: StrategySortKey; label: string }> = [
  { key: 'strategy', label: '전략' },
  { key: 'wilson', label: '신뢰승률' },
  { key: 'sampleSize', label: '고유 거래' },
  { key: 'expectancy', label: '기대값' },
  { key: 'net', label: '순손익' },
  { key: 'openPositions', label: '보유' },
]

function SortableHeader({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
}: {
  label: string
  sortKey: StrategySortKey
  activeKey: StrategySortKey
  direction: SortDirection
  onSort: (key: StrategySortKey) => void
}) {
  const active = activeKey === sortKey
  const nextDirection = active && direction === 'descending' ? '오름차순' : '내림차순'
  return (
    <th aria-sort={active ? direction : undefined}>
      <button
        type="button"
        className={active ? 'strategy-sort-button active' : 'strategy-sort-button'}
        aria-label={`${label} 정렬 · 누르면 ${nextDirection}`}
        onClick={() => onSort(sortKey)}
      >
        <span>{label}</span>
        <span aria-hidden="true">{active ? direction === 'ascending' ? '▲' : '▼' : '↕'}</span>
      </button>
    </th>
  )
}

const lifecycleLabels: Record<StrategyRow['lifecycle'], string> = {
  RESEARCH: '연구 중',
  SHADOW: '독립 검증 중',
  CHALLENGER: '도전자',
  ACTIVE: '현재 대표',
  QUARANTINED: '안전 격리',
  RETIRED: '퇴역·보존',
}

function governanceReason(strategy: StrategyRow, reason: string) {
  if (strategy.reason_code === reason && strategy.reason_ko?.trim()) return strategy.reason_ko
  return '추가 검증 조건을 확인하고 있습니다.'
}

function evaluationTime(timestamp: number) {
  return timestamp > 0 ? new Date(timestamp).toLocaleString('ko-KR') : '시작 설정'
}

function milestoneTiming(value: number | null, sampleSize: number) {
  return value === null || sampleSize === 0
    ? `표본 없음 · ${sampleSize}건`
    : `${formatDurationMs(value)} · ${sampleSize}건`
}

function number(value: string) {
  return Number(value || 0)
}

function sampleStatusLabel(sampleSize: number, status: string) {
  if (sampleSize < 30) return `검증 자료 모으는 중 · ${sampleSize}/30건`
  if (status.includes('PROVEN') && !status.includes('NOT')) return '검증 기준 통과'
  if (status.includes('FAIL') || status.includes('REJECT')) return '검증 기준 미달'
  return '추가 검증 필요'
}

function monitorState(strategy: StrategySummaryRow, accounts: LeagueAccount[]) {
  if (strategy.superseded_by_strategy_id || strategy.is_current_variant === false) {
    return { tone: 'off', label: '이전 버전', detail: '새 평가 중지 · 과거 기록 보존' }
  }
  if (strategy.lifecycle === 'RETIRED') {
    return { tone: 'off', label: '퇴역·보존', detail: strategy.reason_ko || '새 연구 승인 전까지 모의평가 중지' }
  }
  if (strategy.lifecycle === 'QUARANTINED') {
    return { tone: 'fault', label: '안전 격리', detail: strategy.reason_ko || '안전 조건 확인 필요' }
  }
  if (strategy.mode === 'OFF' || (!strategy.long_enabled && !strategy.short_enabled)) {
    return { tone: 'off', label: 'PAPER 연구 꺼짐', detail: strategy.reason_ko || '새 평가 중지 · 과거 기록 보존' }
  }
  if (accounts.some((account) => account.faulted)) return { tone: 'fault', label: '확인 필요', detail: '전략 가상계좌 오류' }
  if (accounts.some((account) => account.paused)) return { tone: 'waiting', label: '안전 대기', detail: '새 진입만 잠시 차단' }
  const openPositions = accounts.reduce((total, account) => total + account.open_positions, 0)
  if (openPositions) return { tone: 'active', label: 'PAPER 진입 중', detail: `${openPositions}건 자동 관리` }
  if (strategy.qualified_paths > 0) return { tone: 'qualified', label: '진입 조건 감지', detail: `${strategy.qualified_paths}개 경로 체결 확인 중` }
  if (strategy.evaluated_paths === 0 || strategy.latest_status === 'WAITING_DATA') return { tone: 'waiting', label: '준비 중', detail: '공개시장 표본을 모으는 중' }
  const reasons = [...new Set(strategy.latest_reasons.map((reason) => strategyWaitReasonLabel(reason, strategy.reason_ko)))].slice(0, 2)
  return { tone: 'watching', label: '조건 미충족', detail: reasons.join(' · ') || '진입 조건 대기' }
}

function currentFamilyStrategies(strategies: StrategySummaryRow[]) {
  const families = new Map<string, StrategySummaryRow[]>()
  for (const strategy of strategies) {
    const familyId = strategy.family_id?.trim() || strategy.strategy_id
    families.set(familyId, [...(families.get(familyId) ?? []), strategy])
  }
  return [...families.values()].flatMap((variants) => {
    const candidates = variants.filter((strategy) => (
      strategy.user_visible_by_default !== false
      && strategy.lifecycle !== 'RETIRED'
      && strategy.role !== 'LEGACY'
      && !strategy.superseded_by_strategy_id
      && strategy.is_current_variant !== false
    ))
    const current = candidates.find((strategy) => strategy.is_current_variant === true)
      ?? candidates[0]
    return current ? [current] : []
  })
}

function placeholderInCategory(family: StrategyFamilyCatalogRow, filter: FamilyCategoryFilter) {
  if (filter === 'all') return true
  if (filter === 'marketNeutral') return family.family_id === 'MARKET_NEUTRAL'
  if (filter === 'filter') {
    return ['POSITIONING_LIQUIDATION', 'MARKET_REGIME_FILTERS', 'SESSION_PROFILE'].includes(family.family_id)
  }
  return false
}

function PlaceholderFamilies({ families }: { families: StrategyFamilyCatalogRow[] }) {
  if (!families.length) return null
  return <section className="family-placeholder-grid" aria-label="실행 준비 중인 전략 family">
    {families.map((family) => <article className="panel" key={family.family_id} data-family-id={family.family_id}>
      <span>{family.category_ko}</span>
      <strong>{family.label_ko}</strong>
      <b>{family.availability_label_ko}</b>
      <p>{family.availability_reason_ko}</p>
      <small>성과 미달 판정이 아니라 runtime 역할·검증 준비 상태입니다.</small>
    </article>)}
  </section>
}

function isDetailedStrategyRow(value: unknown): value is StrategyRow {
  if (!value || typeof value !== 'object') return false
  const strategy = value as Partial<StrategyRow>
  return typeof strategy.strategy_id === 'string'
    && Array.isArray(strategy.expected_holding_seconds)
    && Array.isArray(strategy.entry_rules_ko)
    && Array.isArray(strategy.exit_rules_ko)
    && Boolean(strategy.performance?.BASE && strategy.performance.STRESS)
    && Boolean(strategy.governance && Array.isArray(strategy.governance.change_history))
}

function mergeFamilyDetail(
  restDetail: StrategyFamilyDetail | null,
  streamedDetail: StrategyFamilyDetail | null,
) {
  if (!restDetail) return streamedDetail
  if (!streamedDetail) return restDetail
  const streamedById = new Map(streamedDetail.variants.map((variant) => [variant.strategy_id, variant]))
  const mergedIds = new Set(restDetail.variants.map((variant) => variant.strategy_id))
  const variants = restDetail.variants.map((variant) => {
    const streamed = streamedById.get(variant.strategy_id)
    if (!streamed) return variant
    return {
      ...variant,
      ...streamed,
      setting: variant.setting || streamed.setting
        ? { ...variant.setting, ...streamed.setting }
        : undefined,
      runtime_state: variant.runtime_state || streamed.runtime_state
        ? { ...variant.runtime_state, ...streamed.runtime_state }
        : undefined,
      research_sources: streamed.research_sources ?? variant.research_sources,
    }
  })
  for (const variant of streamedDetail.variants) {
    if (!mergedIds.has(variant.strategy_id)) variants.push(variant)
  }
  return {
    ...restDetail,
    ...streamedDetail,
    variants,
    offline_challengers: streamedDetail.offline_challengers ?? restDetail.offline_challengers,
  }
}

function ProfileDetails({
  report,
  account,
  analyticsReady,
  researchDetails,
}: {
  report: StrategyPerformance
  account: LeagueAccount | undefined
  analyticsReady: boolean
  researchDetails: boolean
}) {
  const windows = ['recent_50', 'recent_100', 'recent_300'] as const
  return (
    <section className="profile-detail-block">
      <h3>{costProfileLabel(report.profile)} 가상계좌</h3>
      <p className="profile-scope-note">자산·순손익은 이번 실행, 완료 표본은 현재 전략 버전의 공개시장 모의거래 기준입니다.</p>
      {!analyticsReady ? <p className="profile-scope-note" role="status">과거 거래기록을 전략 버전별로 확인하고 있습니다. 완료되기 전에는 승률과 손익 통계를 표시하지 않습니다.</p> : null}
      <dl className="drawer-detail-list">
        <div><dt>현재 자산</dt><dd>{formatUsdt(account?.current_equity_usdt ?? '1000', { equity: true })}</dd></div>
        <div><dt>이번 실행 순손익</dt><dd>{formatUsdt(account ? number(account.current_equity_usdt) - number(account.starting_equity_usdt) : 0, { signed: true })}</dd></div>
        <div><dt>고유 진입기회</dt><dd>{profileUniqueSamples(report)}건 <small>· 원장 {report.raw_ledger_row_count ?? report.sample_size}행</small></dd></div>
        <div><dt>승률 · Wilson 하한</dt><dd>{report.win_rate === null ? '아직 표본 없음' : formatPercentFraction(report.win_rate)}<small>{report.win_rate_ci95 ? ` · ${formatPercentFraction(report.win_rate_ci95.lower)}` : ' · 측정 전'}</small></dd></div>
        <div><dt>거래당 기대값</dt><dd>{formatUsdt(report.expectancy_usdt)}</dd></div>
        <div><dt>Profit Factor</dt><dd>{formatRatio(report.profit_factor)}</dd></div>
        <div><dt>현재 버전 순손익</dt><dd>{formatUsdt(report.net_pnl, { signed: true })}</dd></div>
        <div><dt>비용</dt><dd>{formatUsdt(number(report.fees) + number(report.slippage))}<small>· 수수료와 가격차이 포함</small></dd></div>
        <div><dt>최대 낙폭</dt><dd>{formatUsdt(report.maximum_drawdown)}</dd></div>
        <div><dt>Runner 기여</dt><dd>{report.sample_size === 0 ? '측정 전 · NOT_PROVEN' : formatUsdt(report.runner_net_contribution_usdt, { signed: true })}</dd></div>
        <div><dt>보유시간</dt><dd>{report.sample_size === 0 ? '표본 없음' : `보통 ${formatDurationMs(report.median_hold_ms)} · 긴 편 ${formatDurationMs(report.p90_hold_ms)}`}</dd></div>
        <div><dt>현재 판단</dt><dd>{sampleStatusLabel(report.sample_size, report.sample_status)}</dd></div>
      </dl>
      {researchDetails ? <details className="advanced-details"><summary>고급 통계 보기</summary>
        <dl className="drawer-detail-list">
          <div><dt>평균 이익 · 손실</dt><dd>{report.average_win_usdt === null ? '표본 없음' : formatUsdt(report.average_win_usdt)} · {report.average_loss_usdt === null ? '표본 없음' : formatUsdt(report.average_loss_usdt)}</dd></div>
          <div><dt>손익비 · Profit Factor</dt><dd>{formatRatio(report.payoff_ratio)} · {formatRatio(report.profit_factor)}</dd></div>
          <div><dt>기대값 R · bp</dt><dd>{formatRatio(report.expectancy_r, ' R')} · {formatRatio(report.expectancy_bps, ' bp')}</dd></div>
          <div><dt>Omega · 거래당 Sortino</dt><dd>{formatRatio(report.omega_ratio)} · {formatRatio(report.sortino_ratio_per_trade)}</dd></div>
          <div><dt>비연환산 Calmar</dt><dd>{formatRatio(report.calmar_ratio_nonannualized)}</dd></div>
          <div><dt>비용 부담</dt><dd>{formatPercentFraction(report.cost_burden)}</dd></div>
          <div><dt>양방향 거래대금</dt><dd>{formatUsdt(report.turnover_usdt)} · {formatRatio(report.turnover_ratio, 'x')}</dd></div>
          <div><dt>평균 불리·유리 이동</dt><dd>{formatRatio(report.mae_r_mean, ' R')} · {formatRatio(report.mfe_r_mean, ' R')}</dd></div>
          <div><dt>1차·2차 목표까지</dt><dd>{milestoneTiming(report.median_time_to_tp1_ms, report.tp1_sample_size)} · {milestoneTiming(report.median_time_to_tp2_ms, report.tp2_sample_size)}</dd></div>
          <div><dt>손절까지</dt><dd>{milestoneTiming(report.median_time_to_stop_ms, report.stop_sample_size)}</dd></div>
          <div><dt>상승·하락 방향</dt><dd>{report.sides.LONG}건 · {report.sides.SHORT}건</dd></div>
          <div><dt>종목·시장상태</dt><dd>{report.symbols.length}개 · {report.regime_count}개</dd></div>
          <div><dt>과거 버전 제외</dt><dd>{report.excluded_prior_version_samples}건</dd></div>
          <div><dt>기술 판단 코드</dt><dd>{report.sample_status} · {report.recommendation}</dd></div>
        </dl>
        <div className="window-summary">{windows.map((key) => {
          const value = report.windows[key]
          const size = typeof value?.sample_size === 'number' ? value.sample_size : 0
          return <span key={key}>{key.replace('recent_', '최근 ')} · {size}건</span>
        })}</div>
      </details> : null}
    </section>
  )
}

function StrategyOverview({
  strategies,
  leagueAccounts,
  data,
  analyticsReady = true,
  researchDetails = false,
  selectedFamilyDetail: streamedFamilyDetail,
  controlsEnabled,
  onSelectFamily,
  onConfigure,
  onRollback,
  onConfigureFamilyResearch,
}: Props) {
  const [saving, setSaving] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [detailTab, setDetailTab] = useState<StrategyDetailTab>('status')
  const [profile, setProfile] = useState<CostProfile>('BASE')
  const [familyCategory, setFamilyCategory] = useState<FamilyCategoryFilter>('all')
  const [sortKey, setSortKey] = useState<StrategySortKey>('wilson')
  const [sortDirection, setSortDirection] = useState<SortDirection>('descending')
  const [familyDetailResult, setFamilyDetailResult] = useState<StrategyFamilyDetailResult | null>(null)
  const [familyDetailRefreshRevision, setFamilyDetailRefreshRevision] = useState(0)
  const [familyMutation, setFamilyMutation] = useState<{
    familyId: string
    message: string
    undo: FamilyResearchUndo | null
  } | null>(null)
  const [familyMutationError, setFamilyMutationError] = useState('')
  const currentStrategies = useMemo(() => currentFamilyStrategies(strategies), [strategies])
  const categoryStrategies = useMemo(
    () => currentStrategies.filter((strategy) => inFamilyCategory(strategy, familyCategory, profile)),
    [currentStrategies, familyCategory, profile],
  )
  const placeholderFamilies = useMemo(() => {
    const runtimeFamilies = new Set(currentStrategies.map((strategy) => strategy.family_id))
    return (data?.strategy_family_catalog?.families ?? []).filter((family) => (
      family.current_variant_id === null
      && !runtimeFamilies.has(family.family_id)
      && placeholderInCategory(family, familyCategory)
    ))
  }, [currentStrategies, data?.strategy_family_catalog?.families, familyCategory])
  const ordered = useMemo(() => [...categoryStrategies], [categoryStrategies])
  const selectedSummary = useMemo(
    () => strategies.find((strategy) => strategy.strategy_id === selectedId) ?? null,
    [selectedId, strategies],
  )
  const selectedFamilyId = selectedSummary?.family_id
  const restFamilyDetail = selectedFamilyId && familyDetailResult?.familyId === selectedFamilyId
    ? familyDetailResult.detail
    : null
  const activeStreamedFamilyDetail = selectedFamilyId && streamedFamilyDetail?.family_id === selectedFamilyId
    ? streamedFamilyDetail
    : null
  const familyDetail = useMemo(
    () => mergeFamilyDetail(restFamilyDetail, activeStreamedFamilyDetail),
    [activeStreamedFamilyDetail, restFamilyDetail],
  )
  const familyDetailError = selectedFamilyId && familyDetailResult?.familyId === selectedFamilyId
    ? familyDetailResult.error
    : ''
  const familyDetailLoading = Boolean(selectedFamilyId && familyDetailResult?.familyId !== selectedFamilyId)
  const selected = useMemo(() => {
    const runtimeState = familyDetail?.variants.find((variant) => variant.strategy_id === selectedSummary?.strategy_id)?.runtime_state
      ?? familyDetail?.variants.find((variant) => variant.is_current_variant)?.runtime_state
    if (isDetailedStrategyRow(runtimeState)) return runtimeState
    return isDetailedStrategyRow(selectedSummary) ? selectedSummary : null
  }, [familyDetail, selectedSummary])
  const selectedVariant = familyDetail?.variants.find((variant) => variant.strategy_id === selectedSummary?.strategy_id)
    ?? familyDetail?.variants.find((variant) => variant.is_current_variant)
  const previousVariants = familyDetail?.variants.filter((variant) => (
    variant.strategy_id !== selectedVariant?.strategy_id
    && variant.is_current_variant !== true
  )) ?? []
  const selectedFamilySetting = selectedVariant?.setting
  const selectedFamilyMode = selectedFamilySetting?.mode ?? selected?.mode
  const selectedFamilyResearchEnabled = selectedFamilySetting?.research_enabled
    ?? selectedFamilySetting?.enabled
    ?? (selectedFamilyMode ? selectedFamilyMode !== 'OFF' : false)
  const selectedFamilyRevision = selectedFamilySetting?.settings_revision
    ?? selectedFamilySetting?.revision
    ?? selected?.settings_revision
  const selectedFamilyMutation = familyMutation?.familyId === selectedFamilyId
    ? familyMutation
    : null
  useEffect(() => {
    const familyId = selectedFamilyId
    if (!familyId) return
    const controller = new AbortController()
    let disposed = false
    void fetchJson<StrategyFamilyDetail>(
      `/api/strategy-families/${encodeURIComponent(familyId)}`,
      { signal: controller.signal },
    )
      .then((detail) => {
        if (!disposed) setFamilyDetailResult({
          familyId,
          detail: detail.family_id === familyId ? detail : null,
          error: detail.family_id === familyId ? '' : '요청한 전략 family와 다른 상세 응답을 받았습니다.',
        })
      })
      .catch((error: unknown) => {
        if (!disposed) setFamilyDetailResult({ familyId, detail: null, error: requestErrorMessage(error) })
      })
    return () => {
      disposed = true
      controller.abort()
    }
  }, [familyDetailRefreshRevision, selectedFamilyId])
  const configure = useCallback(async (strategy: StrategyRow, configuration: Partial<StrategyConfiguration>) => {
    if (!controlsEnabled) return
    setSaving(strategy.strategy_id)
    try {
      await onConfigure(strategy.strategy_id, {
        mode: strategy.mode,
        long_enabled: strategy.long_enabled,
        short_enabled: strategy.short_enabled,
        expected_revision: strategy.settings_revision,
        ...configuration,
      })
      setFamilyDetailResult(null)
      setFamilyDetailRefreshRevision((revision) => revision + 1)
    } finally {
      setSaving('')
    }
  }, [controlsEnabled, onConfigure])
  const applyFamilyResearchEnabled = useCallback(async (
    familyId: string,
    nextEnabled: boolean,
    expectedRevision: number,
    reason: string,
    message: string,
    undoEnabled?: boolean,
  ) => {
    setSaving(familyId)
    setFamilyMutationError('')
    try {
      if (!controlsEnabled || !onConfigureFamilyResearch) {
        throw new ApiError({
          code: 'PAPER_SAFETY_NOT_VERIFIED',
          messageKo: 'PAPER 안전 상태를 확인할 때까지 전략 변경을 잠급니다.',
        })
      }
      const detail = await onConfigureFamilyResearch(familyId, {
        research_enabled: nextEnabled,
        expected_revision: expectedRevision,
        reason,
      })
      if (detail.family_id !== familyId) throw new Error('family response mismatch')
      const setting = detail.variants.find((variant) => variant.is_current_variant)?.setting
      const responseRevision = setting?.settings_revision ?? setting?.revision
      if (typeof responseRevision !== 'number') throw new Error('missing family revision')
      setFamilyDetailResult({ familyId, detail, error: '' })
      setFamilyMutation({
        familyId,
        message,
        undo: undoEnabled === undefined ? null : {
          familyId,
          researchEnabled: undoEnabled,
          expectedRevision: responseRevision,
        },
      })
    } catch (error) {
      setFamilyMutationError(error instanceof ApiError ? error.messageKo : '모의평가 설정을 변경하지 못했습니다. 최신 상태를 다시 확인해 주세요.')
      if (error instanceof ApiError && error.status === 409) {
        setFamilyDetailResult(null)
        setFamilyDetailRefreshRevision((revision) => revision + 1)
      }
    } finally {
      setSaving('')
    }
  }, [controlsEnabled, onConfigureFamilyResearch])
  const toggleFamilyResearch = useCallback(async (nextEnabled: boolean) => {
    if (!selectedFamilyId || typeof selectedFamilyRevision !== 'number') return
    if (!window.confirm(`이 family의 모의평가를 ${nextEnabled ? '켜' : '끌'}까요? 진행 중 PAPER 포지션과 과거 기록은 그대로 보존됩니다.`)) return
    await applyFamilyResearchEnabled(
      selectedFamilyId,
      nextEnabled,
      selectedFamilyRevision,
      nextEnabled ? 'USER_STRATEGY_CENTER_RESEARCH_ON' : 'USER_STRATEGY_CENTER_RESEARCH_OFF',
      `모의평가를 ${nextEnabled ? '켰습니다' : '껐습니다'}. 진행 포지션과 과거 기록은 보존됩니다.`,
      selectedFamilyResearchEnabled,
    )
  }, [applyFamilyResearchEnabled, selectedFamilyId, selectedFamilyResearchEnabled, selectedFamilyRevision])
  const undoFamilyResearch = useCallback(async () => {
    const undo = familyMutation?.undo
    if (!undo) return
    await applyFamilyResearchEnabled(
      undo.familyId,
      undo.researchEnabled,
      undo.expectedRevision,
      'USER_STRATEGY_CENTER_RESEARCH_UNDO',
      '직전 모의평가 설정으로 되돌렸습니다. 진행 포지션과 과거 기록은 보존됩니다.',
    )
  }, [applyFamilyResearchEnabled, familyMutation?.undo])
  const rollback = useCallback(async (strategy: StrategyRow, targetRevision: number) => {
    if (!controlsEnabled || !onRollback) return
    setSaving(strategy.strategy_id)
    try {
      await onRollback(strategy.strategy_id, targetRevision, strategy.settings_revision)
      setFamilyDetailResult(null)
      setFamilyDetailRefreshRevision((revision) => revision + 1)
    } finally {
      setSaving('')
    }
  }, [controlsEnabled, onRollback])
  const closeDrawer = useCallback(() => {
    setSelectedId('')
    setDetailTab('status')
    setFamilyMutation(null)
    setFamilyMutationError('')
    onSelectFamily?.(null)
  }, [onSelectFamily])
  const accounts = selectedSummary ? leagueAccounts.filter((account) => account.strategy_id === selectedSummary.strategy_id) : []
  const rows = useMemo(() => {
    const accountsByStrategy = new Map<string, LeagueAccount[]>()
    for (const account of leagueAccounts) {
      const strategyAccounts = accountsByStrategy.get(account.strategy_id) ?? []
      strategyAccounts.push(account)
      accountsByStrategy.set(account.strategy_id, strategyAccounts)
    }
    return ordered.map((strategy, originalIndex) => {
      const strategyAccounts = accountsByStrategy.get(strategy.strategy_id) ?? []
      const account = strategyAccounts.find((item) => item.profile === profile)
      const report = strategy.performance[profile]
      return {
        strategy,
        account,
        report,
        monitor: monitorState(strategy, strategyAccounts),
        uniqueSamples: profileUniqueSamples(report),
        wilson: report.win_rate_ci95?.lower == null ? null : number(report.win_rate_ci95.lower),
        expectancy: report.expectancy_usdt == null ? null : number(report.expectancy_usdt),
        net: number(report.net_pnl),
        rankingEligible: profileUniqueSamples(report) >= 30 && strategy.final_ranking_eligible !== false,
        originalIndex,
      }
    }).sort((left, right) => {
      if (sortKey === 'wilson' && left.rankingEligible !== right.rankingEligible) {
        return left.rankingEligible ? -1 : 1
      }
      if (sortKey === 'wilson' && (left.wilson === null || right.wilson === null)) {
        if (left.wilson === right.wilson) return left.originalIndex - right.originalIndex
        return left.wilson === null ? 1 : -1
      }
      let comparison = 0
      if (sortKey === 'strategy') comparison = left.strategy.short_name.localeCompare(right.strategy.short_name, 'ko')
      if (sortKey === 'status') comparison = left.monitor.label.localeCompare(right.monitor.label, 'ko')
      if (sortKey === 'wilson') comparison = (left.wilson ?? 0) - (right.wilson ?? 0)
      if (sortKey === 'sampleSize') comparison = left.uniqueSamples - right.uniqueSamples
      if (sortKey === 'expectancy') comparison = (left.expectancy ?? Number.NEGATIVE_INFINITY) - (right.expectancy ?? Number.NEGATIVE_INFINITY)
      if (sortKey === 'net') comparison = left.net - right.net
      if (sortKey === 'openPositions') comparison = (left.account?.open_positions ?? 0) - (right.account?.open_positions ?? 0)
      const directed = comparison * (sortDirection === 'ascending' ? 1 : -1)
      return directed || left.originalIndex - right.originalIndex
    })
  }, [leagueAccounts, ordered, profile, sortDirection, sortKey])
  const sortBy = useCallback((key: StrategySortKey) => {
    if (key === sortKey) {
      setSortDirection((current) => current === 'ascending' ? 'descending' : 'ascending')
      return
    }
    setSortKey(key)
    setSortDirection(defaultSortDirection[key])
  }, [sortKey])
  const fallbackEnabledCount = strategies.filter((strategy) => strategy.mode !== 'OFF' && (strategy.long_enabled || strategy.short_enabled)).length
  const fallbackDisabledCount = strategies.length - fallbackEnabledCount
  const inventory = data?.strategy_family_catalog?.inventory
  const enabledEntryCandidateCount = inventory?.enabled_directional_entry_candidate_count ?? fallbackEnabledCount
  const inactiveHistoryCount = inventory?.inactive_history_runtime_variant_count ?? fallbackDisabledCount
  const currentRepresentativeCount = inventory?.current_family_entry_representative_count ?? currentStrategies.length
  const catalogVirtualFilterCount = inventory?.catalog_virtual_filter_count ?? 0
  const registeredVariantCount = inventory?.registered_catalog_item_count ?? data?.strategy_family_catalog?.families.reduce(
    (total, family) => total + family.variant_count,
    0,
  ) ?? strategies.length
  const v9Research = data?.strategy_family_catalog?.v9_research
  const v9ManifestComplete = v9Research
    ? v9Research.candidates.length === v9Research.candidate_count
      && v9Research.candidates.filter((candidate) => candidate.monitoring_enabled).length === v9Research.monitoring_on_count
    : false
  const v9AllTrackingOn = v9ManifestComplete
    && v9Research?.monitoring_on_count === v9Research?.candidate_count
  const openPositionCount = leagueAccounts.reduce((total, account) => total + account.open_positions, 0)
  const provenSampleCount = currentStrategies.filter((strategy) => {
    const report = strategy.performance[profile]
    return profileUniqueSamples(report) >= 30
  }).length
  const positiveAfterCostCount = currentStrategies.filter((strategy) => Number(strategy.performance[profile].expectancy_usdt ?? 0) > 0).length
  const topCandidateCount = currentStrategies
    .filter((strategy) => {
      const report = strategy.performance[profile]
      return profileUniqueSamples(report) >= 30
        && strategy.final_ranking_eligible === true
        && Number(report.expectancy_usdt ?? 0) > 0
    })
    .slice(0, 10).length
  return (
    <section aria-labelledby="strategies-heading">
      <div className="page-heading"><div><p className="section-kicker">전략 family별 모의결과</p><h2 id="strategies-heading">전략 한눈에 보기</h2><p className="heading-help">현재 variant의 신뢰승률 하한, 표본, 기대값과 비용 후 순손익을 함께 봅니다.</p></div><span className="page-note">{costProfileLabel(profile)} · 화면 대표 {currentRepresentativeCount}개 · 기존 등록 {registeredVariantCount}개</span></div>
      <section className="strategy-summary-strip" aria-label="전략 모의평가 요약">
        <article><span>방향 진입 후보 ON</span><b>{enabledEntryCandidateCount}</b></article>
        <article><span>기존 보존·중지</span><b>{inactiveHistoryCount}</b></article>
        <article><span>표시 중인 family 대표</span><b>{currentRepresentativeCount}</b></article>
        <article><span>기존 전체 등록</span><b>{registeredVariantCount}</b></article>
        <article><span>진행 포지션</span><b>{openPositionCount}</b></article>
        <article><span>30건 이상</span><b>{provenSampleCount}</b></article>
        <article><span>비용후 양수</span><b>{positiveAfterCostCount}</b></article>
        <article><span>TOP10 후보</span><b>{topCandidateCount}</b></article>
      </section>
      <p className="strategy-inventory-note">기본 표의 {currentRepresentativeCount}개는 family별 화면 대표이며 ACTIVE 승격을 뜻하지 않습니다. 현재·도전자를 합친 방향 진입 후보 {enabledEntryCandidateCount}개만 독립 SHADOW 모의평가 ON입니다. 기존 전체 등록 {registeredVariantCount}개에는 보존된 이전·legacy {inactiveHistoryCount}개와 진입하지 않는 실행확인 필터 {catalogVirtualFilterCount}개가 포함됩니다.</p>
      {v9Research ? <section className="v9-research-catalog" aria-label="V9 연구 추적 상태">
        <div className="v9-research-heading">
          <div><span>V9 연구 모듈·후보</span><strong>연구 추적 ON {v9Research.monitoring_on_count}/{v9Research.candidate_count}</strong></div>
          <p>새 방향 전략 {v9Research.direction_strategy_count}개 · 새 시장중립 {v9Research.market_neutral_strategy_count}개 · PAPER 진입 활성 {v9Research.entry_enabled_count}개</p>
        </div>
        <div className="v9-research-grid">{v9Research.candidates.map((candidate) => <article key={candidate.candidate_id}>
          <div><b>{candidate.label_ko}</b><small>{v9RoleLabels[candidate.role] ?? candidate.role} · {v9ReadinessLabels[candidate.readiness] ?? candidate.readiness}</small><small>등록 출처 {candidate.source_ids.length}개</small><code>{candidate.candidate_id}</code></div>
          <span className={candidate.monitoring_enabled ? 'v9-monitor-on' : 'v9-monitor-off'}>{candidate.monitoring_enabled ? '추적 ON' : '추적 OFF'}</span>
          <em>{candidate.entry_enabled ? 'PAPER 진입 ON' : '검증 전 진입 차단'}</em>
        </article>)}</div>
        <p>{!v9ManifestComplete ? `연구 추적 목록 계약을 확인해야 합니다. 화면 ${v9Research.candidates.length}개 · 선언 ${v9Research.candidate_count}개입니다.` : v9AllTrackingOn ? `${v9Research.candidate_count}개 항목의 연구 추적 스위치는 모두 ON입니다.` : `연구 추적 ${v9Research.monitoring_on_count}/${v9Research.candidate_count}개가 ON입니다.`} 추적 ON은 읽기 전용 연구 상태이며 PAPER 진입 스위치가 아닙니다. 필터·라우터·통계는 방향 전략 개수에 합산하지 않고 검증 전 후보는 ACTIVE나 주문을 만들지 않습니다.</p>
      </section> : null}
      {!analyticsReady ? <p className="profile-scope-note" role="status">과거 거래통계를 전략 버전별로 불러오는 중입니다. 준비 전 숫자는 순위나 승률로 사용하지 않습니다.</p> : null}
      <div className="family-category-tabs" role="tablist" aria-label="전략 family 분류">{familyCategoryOptions.map((option) => <button type="button" role="tab" aria-selected={familyCategory === option.id} key={option.id} onClick={() => setFamilyCategory(option.id)}>{option.label}</button>)}</div>
      {currentStrategies.length === 0 ? <div className="panel empty-state"><b>전략 정보를 불러오는 중입니다.</b></div> : null}
      {familyCategory === 'all' || familyCategory === 'filter' ? <OrderflowFilterPanel strategies={strategies} controlsEnabled={controlsEnabled} onConfigureFamilyResearch={onConfigureFamilyResearch} /> : null}
      <PlaceholderFamilies families={placeholderFamilies} />
      {currentStrategies.length > 0 && ordered.length === 0 ? <div className="panel empty-state"><b>{familyCategory === 'ranking' ? '아직 순위에 넣을 전략이 없습니다.' : '이 분류에서 표시할 현재 전략이 없습니다.'}</b><span>{familyCategory === 'ranking' ? '고유 진입기회 30건과 최종 순위 자격을 모두 충족해야 표시합니다.' : '실행 전 연구 family는 조건 정보가 준비되면 표시합니다.'}</span></div> : null}
      <section className="panel strategy-compact-panel">
        <div className="strategy-table-toolbar">
          <div><strong>{costProfileLabel(profile)} 기준</strong><span>현재 전략 버전 · 공개시장 PAPER만</span></div>
          <div className="segmented-control" role="group" aria-label="성과 비용 기준">
            <button type="button" className={profile === 'BASE' ? 'selected' : ''} aria-pressed={profile === 'BASE'} onClick={() => setProfile('BASE')}>기본 비용</button>
            <button type="button" className={profile === 'STRESS' ? 'selected' : ''} aria-pressed={profile === 'STRESS'} onClick={() => setProfile('STRESS')}>보수 비용</button>
          </div>
        </div>
        <p className="strategy-ranking-note">30건 미만 승률은 참고값이며 순위나 수익성 결론으로 사용하지 않습니다. 보수 비용은 더 불리한 수수료·가격차이를 적용합니다.</p>
        <div className="strategy-mobile-sort" role="group" aria-label="전략표 정렬">{mobileSortOptions.map((option) => <button type="button" className={sortKey === option.key ? 'active' : ''} aria-pressed={sortKey === option.key} key={option.key} onClick={() => sortBy(option.key)}>{option.label}<span aria-hidden="true">{sortKey === option.key ? sortDirection === 'ascending' ? ' ▲' : ' ▼' : ''}</span></button>)}</div>
        <div className="table-scroll"><table className="strategy-compact-table"><thead><tr>
          <SortableHeader label="전략" sortKey="strategy" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <th>모의평가</th>
          <SortableHeader label="지금 상태" sortKey="status" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <SortableHeader label="신뢰승률" sortKey="wilson" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <SortableHeader label="고유 거래" sortKey="sampleSize" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <SortableHeader label="기대값" sortKey="expectancy" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <SortableHeader label="순손익" sortKey="net" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <th>PF</th>
          <th>주요 대기이유</th>
          <SortableHeader label="보유" sortKey="openPositions" activeKey={sortKey} direction={sortDirection} onSort={sortBy} />
          <th>보기</th>
        </tr></thead><tbody>{rows.map(({ strategy, account, report, monitor, uniqueSamples, wilson, expectancy, net }) => {
          const confidenceRate = !analyticsReady ? '불러오는 중' : uniqueSamples < 30 ? '순위 제외' : wilson === null ? '계산 대기' : formatPercentFraction(wilson)
          const evaluationEnabled = strategy.mode !== 'OFF' && (strategy.long_enabled || strategy.short_enabled)
          return <tr key={strategy.strategy_id} data-strategy-id={strategy.strategy_id}>
            <td data-label="전략"><strong>{strategy.family_label_ko || strategy.short_name}</strong><small>{strategy.variant_label_ko || strategy.display_name_ko} · {lifecycleLabels[strategy.lifecycle]}</small></td>
            <td data-label="모의평가"><span className={evaluationEnabled ? 'strategy-evaluation-state on' : 'strategy-evaluation-state off'}>{evaluationEnabled ? 'ON' : 'OFF'}</span></td>
            <td data-label="지금 상태"><span className={`strategy-monitor ${monitor.tone}`}>{monitor.label}</span></td>
            <td data-label="신뢰승률"><strong>{confidenceRate}</strong><small>{report.win_rate === null ? '관측 승률 없음' : `관측 ${formatPercentFraction(report.win_rate)}`}</small></td>
            <td data-label="고유 거래"><strong>{analyticsReady ? `${uniqueSamples}건` : '불러오는 중'}</strong><small>{report.raw_ledger_row_count && report.raw_ledger_row_count !== uniqueSamples ? `원장 ${report.raw_ledger_row_count}행 · 비용쌍 묶음` : uniqueSamples < 30 ? `${30 - uniqueSamples}건 더 필요` : sampleStatusLabel(uniqueSamples, report.sample_status)}</small></td>
            <td data-label="기대값"><strong>{expectancy === null ? '계산 대기' : formatUsdt(expectancy, { signed: true })}</strong><small>비용 후 거래당</small></td>
            <td data-label="순손익"><span className={net > 0 ? 'positive' : net < 0 ? 'negative' : ''}>{formatUsdt(net, { signed: true })}</span><small>현재 전략 버전</small></td>
            <td data-label="PF"><strong>{formatRatio(report.profit_factor)}</strong><small>비용 후</small></td>
            <td data-label="주요 대기이유"><span>{monitor.detail}</span></td>
            <td data-label="보유"><strong>{account?.open_positions ?? 0}건</strong><small>{costProfileLabel(profile)}</small></td>
            <td data-label="보기"><button type="button" className="secondary-button" onClick={() => { setDetailTab('status'); setFamilyMutation(null); setFamilyMutationError(''); setSelectedId(strategy.strategy_id); onSelectFamily?.(strategy.family_id ?? null) }}>자세히·설정</button></td>
          </tr>
        })}</tbody></table></div>
      </section>
      <SideDrawer title={selectedSummary ? `${selectedSummary.short_name} · ${selectedSummary.display_name_ko}` : '전략 상세'} open={selectedSummary !== null} onClose={closeDrawer} label="전략 상세 정보">
        {selectedSummary ? <>
          <p className="drawer-subtitle"><b>{lifecycleLabels[selectedSummary.lifecycle]}</b> · {modeLabels[selectedSummary.mode]}</p>
          <div className="strategy-detail-tabs" role="tablist" aria-label="전략 상세 항목">
            {strategyDetailTabs.map((option, index) => <button
              type="button"
              role="tab"
              id={`strategy-detail-tab-${option.id}`}
              aria-controls={`strategy-detail-panel-${option.id}`}
              aria-selected={detailTab === option.id}
              tabIndex={detailTab === option.id ? 0 : -1}
              key={option.id}
              onClick={() => setDetailTab(option.id)}
              onKeyDown={(event) => {
                let nextIndex: number | null = null
                if (event.key === 'ArrowRight') nextIndex = (index + 1) % strategyDetailTabs.length
                if (event.key === 'ArrowLeft') nextIndex = (index - 1 + strategyDetailTabs.length) % strategyDetailTabs.length
                if (event.key === 'Home') nextIndex = 0
                if (event.key === 'End') nextIndex = strategyDetailTabs.length - 1
                if (nextIndex === null) return
                event.preventDefault()
                const next = strategyDetailTabs[nextIndex]
                setDetailTab(next.id)
                document.getElementById(`strategy-detail-tab-${next.id}`)?.focus()
              }}
            >{option.label}</button>)}
          </div>
          <div
            id={`strategy-detail-panel-${detailTab}`}
            role="tabpanel"
            aria-labelledby={`strategy-detail-tab-${detailTab}`}
            tabIndex={0}
          >
          {detailTab === 'status' && selectedFamilyId ? <section className="profile-detail-block">
            <h3>전략 family</h3>
            {familyDetailLoading ? <p role="status">family 상세를 불러오는 중입니다.</p> : familyDetail ? <><p>{familyDetail.description_ko || '전략 설명을 준비하고 있습니다.'}</p><dl className="drawer-detail-list"><div><dt>분류</dt><dd>{familyDetail.category_ko || '분류 확인 중'}</dd></div><div><dt>현재 variant</dt><dd>{familyDetail.variants.find((variant) => variant.is_current_variant)?.variant_label_ko ?? selectedSummary.variant_label_ko ?? selectedSummary.display_name_ko}</dd></div>{researchDetails ? <div><dt>오프라인 후보</dt><dd>{familyDetail.offline_challengers?.length ?? 0}개 · 실행 전 연구</dd></div> : null}</dl></> : <p>{familyDetailError || '현재 dashboard의 family·variant 정보로 표시합니다.'}</p>}
          </section> : null}
          {detailTab === 'previous' && familyDetail ? <section className="profile-detail-block strategy-previous-variants">
            <h3>이전 버전</h3>
            {previousVariants.length ? <ul>{previousVariants.map((variant) => <li key={variant.strategy_id}><strong>{variant.variant_label_ko || variant.strategy_id}</strong><span>{variant.setting?.lifecycle || variant.role || '기록 보존'} · {variant.setting?.mode || '실행 안 함'}</span></li>)}</ul> : <p>등록된 이전 runtime variant가 없습니다.</p>}
          </section> : null}
          {detailTab === 'previous' && !familyDetail ? <section className="profile-detail-block"><h3>이전 버전</h3><p role="status">{familyDetailLoading ? '이전 버전 기록을 불러오는 중입니다.' : familyDetailError || '등록된 이전 버전 정보를 확인하지 못했습니다.'}</p></section> : null}
          {selectedFamilyId && (detailTab === 'status' || detailTab === 'conditions' || detailTab === 'exit') ? <div>
            <StrategyConditionsPanel familyId={selectedFamilyId} view={detailTab} />
          </div> : null}
          {selected ? <>
          {detailTab === 'status' ? <><section className="profile-detail-block strategy-drawer-settings">
            <h3>작동 설정</h3>
            {selected.policy_reactivation_locked ? <div className="strategy-retired-note"><strong>검증 종료</strong><span>새 진입 없음 · 거래기록과 가상계좌 보존</span><span>과거 상승·하락 설정만 보존합니다.</span></div> : <>
              <p className="profile-scope-note">설정을 바꿔도 진행 중인 PAPER 포지션은 기존 진입 계획대로 관리됩니다.</p>
              <span className="strategy-setting-label">모의평가</span>
              {selected.mode === 'ACTIVE' ? <p className="profile-scope-note">공동 PAPER 참여는 검증 gate를 통과한 Governor만 정합니다. 사용자는 독립 검증 또는 중지로만 낮출 수 있습니다.</p> : null}
              <div className="strategy-inline-modes">
                <button type="button" aria-label={`${selected.short_name} 모의평가 켜기`} aria-pressed={selectedFamilyResearchEnabled} disabled={!controlsEnabled || !onConfigureFamilyResearch || !selectedFamilyId || typeof selectedFamilyRevision !== 'number' || saving === selectedFamilyId} onClick={() => { if (!selectedFamilyResearchEnabled) void toggleFamilyResearch(true) }}>{saving === selectedFamilyId && !selectedFamilyResearchEnabled ? '저장 중' : 'ON'}</button>
                <button type="button" aria-label={`${selected.short_name} 모의평가 끄기`} aria-pressed={!selectedFamilyResearchEnabled} disabled={!controlsEnabled || !onConfigureFamilyResearch || !selectedFamilyId || typeof selectedFamilyRevision !== 'number' || saving === selectedFamilyId} onClick={() => { if (selectedFamilyResearchEnabled) void toggleFamilyResearch(false) }}>{saving === selectedFamilyId && selectedFamilyResearchEnabled ? '저장 중' : 'OFF'}</button>
              </div>
              <p className="profile-scope-note">OFF는 신규 평가·신규 entry만 중지하며 진행 포지션과 과거 기록을 보존합니다.</p>
              {selectedFamilyMutation ? <div className="strategy-mutation-toast" role="status"><span>{selectedFamilyMutation.message}</span>{selectedFamilyMutation.undo ? <button type="button" className="secondary-button" disabled={!controlsEnabled || !onConfigureFamilyResearch || saving === selectedFamilyId} onClick={() => void undoFamilyResearch()}>실행 취소</button> : null}</div> : null}
              {familyMutationError ? <p className="strategy-condition-error" role="alert">{familyMutationError}</p> : null}
              <span className="strategy-setting-label">거래 방향</span>
              <div className="strategy-inline-directions"><button type="button" aria-pressed={selected.long_enabled} disabled={!controlsEnabled || saving === selected.strategy_id || saving === selectedFamilyId} onClick={() => void configure(selected, { long_enabled: !selected.long_enabled })}>상승 {selected.long_enabled ? '켜짐' : '꺼짐'}</button><button type="button" aria-pressed={selected.short_enabled} disabled={!controlsEnabled || saving === selected.strategy_id || saving === selectedFamilyId} onClick={() => void configure(selected, { short_enabled: !selected.short_enabled })}>하락 {selected.short_enabled ? '켜짐' : '꺼짐'}</button></div>
              <p className="profile-scope-note">{selected.manual_lock ? '사용자가 고정한 설정입니다.' : '검증 결과에 따라 안전하게 자동 관리됩니다.'}</p>
            </>}
          </section>
          <section className="profile-detail-block">
            <h3>한눈에 보는 전략</h3>
            <dl className="drawer-detail-list">
              <div><dt>예상 보유</dt><dd>{formatDurationMs(selected.expected_holding_seconds[0] * 1_000)}~{formatDurationMs(selected.expected_holding_seconds[1] * 1_000)}</dd></div>
              <div><dt>이익 목표</dt><dd>1차 {selected.take_profit_1_r}R · 2차 {selected.take_profit_2_r}R</dd></div>
              <div><dt>최소 준비</dt><dd>{selected.minimum_warmup_ko}</dd></div>
              <div><dt>무엇을 노리나요?</dt><dd>{selected.entry_hypothesis_ko}</dd></div>
              <div><dt>종료 원칙</dt><dd>{selected.edge_decay_policy_ko}</dd></div>
            </dl>
            {researchDetails ? <details className="advanced-details"><summary>고급 기술 정보</summary><dl className="drawer-detail-list"><div><dt>전략 코드</dt><dd>{selected.strategy_id}</dd></div><div><dt>전략 시간축</dt><dd>{selected.horizon_class}</dd></div><div><dt>신호 반감기</dt><dd>{selected.signal_half_life_seconds}초</dd></div><div><dt>사용 시간구간</dt><dd>{selected.required_timeframes.join(' · ')}</dd></div><div><dt>자동 관리 모델</dt><dd>{selected.exit_model} · {selected.max_hold_seconds === null ? '시간청산 없음' : `최대 ${formatDurationMs(selected.max_hold_seconds * 1_000)}`}</dd></div><div><dt>비용 모델</dt><dd>{selected.cost_model_version}</dd></div><div><dt>전략 버전</dt><dd>{selected.strategy_version}</dd></div><div><dt>필요 데이터</dt><dd>{selected.required_market_data.join(' · ')}</dd></div><div><dt>반증 조건</dt><dd>{selected.falsification_conditions_ko.join(' · ')}</dd></div><div><dt>위험예산</dt><dd>{selected.risk_budget_rule_ko}</dd></div><div><dt>대상 범위</dt><dd>{selected.target_universe_ko} · {selected.supported_regimes.join(' · ')}</dd></div><div><dt>미래정보 방지</dt><dd>{selected.data_leakage_guards_ko.join(' · ')}</dd></div><div><dt>현재 상태 코드</dt><dd>{selected.change_reason}</dd></div><div><dt>설정 개정</dt><dd>rev {selected.settings_revision} · {selected.changed_by}</dd></div></dl></details> : null}
          </section></> : null}
          {detailTab === 'sources' ? <div><ResearchSources sources={selectedVariant?.research_sources} sourceIds={selected.research_source_ids} /></div> : null}
          {(detailTab === 'conditions' && selected.entry_rules_ko.length) || (detailTab === 'exit' && selected.exit_rules_ko.length) ? <section className="profile-detail-block">
            <h3>{detailTab === 'conditions' ? '진입 규칙 설명' : '청산 규칙 설명'}</h3>
            <dl className="drawer-detail-list">
              {detailTab === 'conditions' ? <div><dt>진입 조건</dt><dd>{selected.entry_rules_ko.join(' · ')}</dd></div> : null}
              {detailTab === 'exit' ? <div><dt>종료 규칙</dt><dd>{selected.exit_rules_ko.join(' · ')}</dd></div> : null}
            </dl>
            <p className="profile-scope-note">아직 수익성이 입증되지 않은 독립 PAPER 검증 전략이며 공동계좌에는 연결되지 않습니다.</p>
          </section> : null}
          {detailTab === 'status' && researchDetails ? <section className="profile-detail-block">
            <h3>자동 평가 상태</h3>
            <dl className="drawer-detail-list">
              <div><dt>공동계좌 현재 대표</dt><dd>{selected.governance.champion_id ? summaryStrategyLabel(strategies.find((item) => item.strategy_id === selected.governance.champion_id), selected.governance.champion_id) : selected.lifecycle === 'ACTIVE' ? selected.short_name : '없음'}</dd></div>
              <div><dt>마지막 평가</dt><dd>{evaluationTime(selected.governance.last_evaluated_ts_ms)}</dd></div>
              <div><dt>검증 결론</dt><dd>{selected.governance.evidence_status === 'PROVEN' ? '검증됨' : '아직 검증 불충분'}</dd></div>
              <div><dt>다음 평가까지</dt><dd>{selected.governance.remaining_live_samples}건 · {selected.governance.remaining_days.toFixed(1)}일 더 필요</dd></div>
              <div><dt>현재 이유</dt><dd>{selected.governance.reason_codes.slice(0, 4).map((reason) => governanceReason(selected, reason)).join(' ')}</dd></div>
              <div><dt>자동 변경</dt><dd>{selected.manual_lock ? '사용자 고정으로 차단' : selected.governance.automatic_action_allowed ? '검증된 전환 가능' : '현재 조건에서 변경 없음'}</dd></div>
            </dl>
            <details><summary>변경 이력</summary><ol>{selected.governance.change_history.map((row) => <li key={row.transition_id}><strong>rev {row.response_revision}</strong> · {row.description_ko}<small>{row.previous_state} → {row.new_state} · {row.actor} · {row.cause_code} · {evaluationTime(row.occurred_ts_ms)}</small></li>)}</ol></details>
            {selected.policy_reactivation_locked ? <p className="profile-scope-note">비용후 검증으로 퇴역한 전략입니다. 과거 변경 기록은 보존되지만 새 연구 승인 전에는 복원할 수 없습니다.</p> : null}
            {onRollback && !selected.policy_reactivation_locked && selected.governance.change_history.length > 1 ? <button type="button" className="secondary-button" disabled={!controlsEnabled || saving === selected.strategy_id} onClick={() => {
              const previous = selected.governance.change_history.at(-2)
              if (previous && window.confirm(`${selected.short_name} 설정을 rev ${previous.settings_revision}로 복원할까요? 현재 기록은 삭제되지 않습니다.`)) void rollback(selected, previous.settings_revision)
            }}>직전 설정으로 복원</button> : null}
          </section> : null}
          {detailTab === 'performance' ? <div>
            <div className="segmented-control strategy-detail-profile-toggle" role="group" aria-label="전략 상세 성과 비용 기준">
              <button type="button" className={profile === 'BASE' ? 'selected' : ''} aria-pressed={profile === 'BASE'} onClick={() => setProfile('BASE')}>기본 비용</button>
              <button type="button" className={profile === 'STRESS' ? 'selected' : ''} aria-pressed={profile === 'STRESS'} onClick={() => setProfile('STRESS')}>보수 비용</button>
            </div>
            <ProfileDetails report={selected.performance[profile]} account={accounts.find((account) => account.profile === profile)} analyticsReady={analyticsReady} researchDetails={researchDetails} />
          </div> : null}
          </> : <section className="profile-detail-block"><h3>전략 상세</h3><p role="status">{familyDetailLoading ? '전략의 전체 규칙과 설정을 불러오는 중입니다.' : familyDetailError || '전체 전략 상세값이 아직 없습니다. 요약표는 볼 수 있지만 설정을 변경하지 않습니다.'}</p></section>}
          </div>
        </> : null}
      </SideDrawer>
    </section>
  )
}

type StrategyTab = 'overview' | 'performance' | 'symbols'

export function StrategiesPage(props: Props) {
  const [tab, setTab] = useState<StrategyTab>('overview')
  const visibleStrategies = useMemo(() => currentFamilyStrategies(props.strategies), [props.strategies])
  return (
    <section className="strategy-center" aria-label="전략 센터">
      <div className="page-tabs" role="tablist" aria-label="전략 화면">
        <button type="button" role="tab" aria-selected={tab === 'overview'} onClick={() => setTab('overview')}>전체</button>
        <button type="button" role="tab" aria-selected={tab === 'performance'} onClick={() => setTab('performance')}>성과</button>
        <button type="button" role="tab" aria-selected={tab === 'symbols'} onClick={() => setTab('symbols')}>종목별</button>
      </div>
      {tab === 'overview' ? <div role="tabpanel" aria-label="전체"><StrategyOverview {...props} /></div> : null}
      {tab === 'performance' ? <div role="tabpanel" aria-label="성과">{props.data ? <StrategyPerformancePanel data={props.data} strategies={visibleStrategies} leagueAccounts={props.leagueAccounts} history={props.history ?? []} /> : <p className="empty-copy">성과 정보를 불러오는 중입니다.</p>}</div> : null}
      {tab === 'symbols' ? <div role="tabpanel" aria-label="종목별"><StrategySymbolPanel strategies={visibleStrategies} /></div> : null}
    </section>
  )
}
