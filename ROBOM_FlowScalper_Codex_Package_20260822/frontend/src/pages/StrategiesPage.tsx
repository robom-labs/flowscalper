// 네 전략의 모의 실행 방식과 상승·하락 방향을 쉬운 말로 제어한다.
import { useState } from 'react'
import type { ShadowAccount, StrategyRow } from '../types'

type StrategyConfiguration = {
  mode: 'ACTIVE' | 'SHADOW' | 'OFF'
  long_enabled: boolean
  short_enabled: boolean
}

type Props = {
  strategies: StrategyRow[]
  shadowAccounts: ShadowAccount[]
  onConfigure: (strategyId: string, configuration: StrategyConfiguration) => Promise<unknown>
}

const modeLabels = { ACTIVE: '자동 모의매매', SHADOW: '기록만 하기', OFF: '사용 안 함' } as const

function metric(value: string | number | null, suffix = '') {
  return value === null ? '표본 없음' : `${value}${suffix}`
}

export function StrategiesPage({ strategies, shadowAccounts, onConfigure }: Props) {
  const [saving, setSaving] = useState('')
  const configure = async (strategy: StrategyRow, configuration: Partial<StrategyConfiguration>) => {
    setSaving(strategy.strategy_id)
    try {
      await onConfigure(strategy.strategy_id, {
        mode: strategy.mode,
        long_enabled: strategy.long_enabled,
        short_enabled: strategy.short_enabled,
        ...configuration,
      })
    } finally {
      setSaving('')
    }
  }

  return (
    <section aria-labelledby="strategies-heading">
      <div className="page-heading">
        <div>
          <p className="section-kicker">STRATEGY REGISTRY</p>
          <h2 id="strategies-heading">매매 설정</h2>
          <p className="heading-help">전략별로 모의거래에 사용할지, 결과만 기록할지, 끌지를 선택합니다.</p>
        </div>
        <span className="page-note">모든 설정은 모의거래에만 적용됩니다.</span>
      </div>
      {strategies.length === 0 ? <div className="panel empty-state"><b>전략 레지스트리를 불러오는 중입니다</b><p>연결이 복구되면 네 전략을 자동으로 표시합니다.</p></div> : null}
      <div className="strategy-grid">
        {strategies.map((strategy) => {
          const base = strategy.performance.BASE
          const stress = strategy.performance.STRESS
          const accounts = shadowAccounts.filter((account) => account.strategy_id === strategy.strategy_id)
          return (
            <article className="panel strategy-card" key={strategy.strategy_id}>
              <div className="strategy-card-heading">
                <div>
                  <span className={strategy.stability === 'STABLE' ? 'strategy-stability stable' : 'strategy-stability experimental'}>{strategy.stability === 'STABLE' ? '기본 전략' : '시험 중인 전략'}</span>
                  <h3>{strategy.short_name} · {strategy.display_name_ko}</h3>
                  <p>{strategy.summary_ko}</p>
                </div>
                <span className="strategy-state">{saving === strategy.strategy_id ? '저장 중' : modeLabels[strategy.mode]}</span>
              </div>
              <fieldset className="mode-control" disabled={saving === strategy.strategy_id}>
                <legend>이 전략을 어떻게 사용할까요?</legend>
                {(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => <button type="button" className={strategy.mode === mode ? 'selected' : ''} aria-pressed={strategy.mode === mode} key={mode} onClick={() => void configure(strategy, { mode })}>{modeLabels[mode]}</button>)}
              </fieldset>
              <div className="direction-controls">
                <button type="button" className={strategy.long_enabled ? 'direction-on' : 'direction-off'} aria-pressed={strategy.long_enabled} disabled={saving === strategy.strategy_id} onClick={() => void configure(strategy, { long_enabled: !strategy.long_enabled })}>상승 방향 {strategy.long_enabled ? '사용' : '사용 안 함'}</button>
                <button type="button" className={strategy.short_enabled ? 'direction-on' : 'direction-off'} aria-pressed={strategy.short_enabled} disabled={saving === strategy.strategy_id} onClick={() => void configure(strategy, { short_enabled: !strategy.short_enabled })}>하락 방향 {strategy.short_enabled ? '사용' : '사용 안 함'}</button>
              </div>
              <p className="strategy-latest">현재 상태 · {strategy.latest_status === 'WAITING_DATA' ? '시장 데이터 기다리는 중' : strategy.latest_status}<small>{strategy.qualified_paths > 0 ? `조건에 맞은 경우 ${strategy.qualified_paths}회` : '아직 진입 조건에 맞은 경우가 없습니다.'}</small></p>
              <details className="advanced-details">
                <summary>고급 설정과 성과 보기</summary>
                <div className="strategy-metrics">
                  <div><span>평가 / 통과</span><b>{strategy.evaluated_paths} / {strategy.qualified_paths}</b></div>
                  <div><span>기대 손익</span><b>{metric(base.expectancy_usdt, ' USDT')}</b></div>
                  <div><span>수익 효율</span><b>{metric(base.profit_factor)}</b></div>
                  <div><span>표본상태</span><b>{base.sample_status}</b></div>
                </div>
                <div className="profile-comparison">
                  {[base, stress].map((report) => <dl key={report.profile}><div><dt>비용 프로필</dt><dd>{report.profile}</dd></div><div><dt>표본</dt><dd>{report.sample_size}건</dd></div><div><dt>승률</dt><dd>{report.win_rate === null ? '표본 없음' : `${(Number(report.win_rate) * 100).toFixed(1)}%`}</dd></div><div><dt>기대값</dt><dd>{metric(report.expectancy_usdt, ' USDT')}</dd></div><div><dt>Profit Factor</dt><dd>{metric(report.profit_factor)}</dd></div><div><dt>수수료 / 슬리피지</dt><dd>{report.fees} / {report.slippage}</dd></div><div><dt>최대 낙폭</dt><dd>{report.maximum_drawdown} USDT</dd></div><div><dt>판단</dt><dd>{report.recommendation} · 참고용</dd></div></dl>)}
                </div>
                <div className="account-list">{accounts.map((account) => <span key={`${account.strategy_id}-${account.profile}`}>{account.profile} 독립계좌 {account.current_equity_usdt} USDT · 거래 {account.closed_trades}건</span>)}</div>
              </details>
            </article>
          )
        })}
      </div>
    </section>
  )
}
