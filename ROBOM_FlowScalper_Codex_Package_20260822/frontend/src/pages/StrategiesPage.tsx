// 네 전략의 실행 모드·방향 허용·독립 PAPER 성과를 한 화면에서 제어한다.
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

const modeLabels = { ACTIVE: '실전 PAPER', SHADOW: '가상 관찰', OFF: '끄기' } as const

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
          <h2 id="strategies-heading">전략 관리</h2>
          <p className="heading-help">실전 PAPER는 주계좌 후보가 되고, 가상 관찰은 독립 계좌에서만 성과를 쌓습니다.</p>
        </div>
        <span className="page-note">A·B 안정 전략 · C·D PAPER 실험 전략</span>
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
                  <span className={strategy.stability === 'STABLE' ? 'strategy-stability stable' : 'strategy-stability experimental'}>{strategy.stability === 'STABLE' ? '안정 전략' : 'PAPER 실험 전략'}</span>
                  <h3>{strategy.short_name} · {strategy.display_name_ko}</h3>
                  <p>{strategy.summary_ko}</p>
                </div>
                <span className="strategy-state">{saving === strategy.strategy_id ? '저장 중' : modeLabels[strategy.mode]}</span>
              </div>
              <fieldset className="mode-control" disabled={saving === strategy.strategy_id}>
                <legend>실행 방식</legend>
                {(['ACTIVE', 'SHADOW', 'OFF'] as const).map((mode) => <button type="button" className={strategy.mode === mode ? 'selected' : ''} aria-pressed={strategy.mode === mode} key={mode} onClick={() => void configure(strategy, { mode })}>{modeLabels[mode]}</button>)}
              </fieldset>
              <div className="direction-controls">
                <button type="button" className={strategy.long_enabled ? 'direction-on' : 'direction-off'} aria-pressed={strategy.long_enabled} disabled={saving === strategy.strategy_id} onClick={() => void configure(strategy, { long_enabled: !strategy.long_enabled })}>LONG {strategy.long_enabled ? '허용' : '차단'}</button>
                <button type="button" className={strategy.short_enabled ? 'direction-on' : 'direction-off'} aria-pressed={strategy.short_enabled} disabled={saving === strategy.strategy_id} onClick={() => void configure(strategy, { short_enabled: !strategy.short_enabled })}>SHORT {strategy.short_enabled ? '허용' : '차단'}</button>
              </div>
              <div className="strategy-metrics">
                <div><span>평가 / 통과</span><b>{strategy.evaluated_paths} / {strategy.qualified_paths}</b></div>
                <div><span>BASE 기대값</span><b>{metric(base.expectancy_usdt, ' USDT')}</b></div>
                <div><span>Profit Factor</span><b>{metric(base.profit_factor)}</b></div>
                <div><span>표본상태</span><b>{base.sample_status}</b></div>
              </div>
              <p className="strategy-latest">현재 판단 · {strategy.latest_status === 'WAITING_DATA' ? '데이터 대기' : strategy.latest_status}<small>{strategy.latest_reasons.length > 0 ? strategy.latest_reasons.join(' · ') : '아직 평가 근거가 없습니다.'}</small></p>
              <details className="advanced-details">
                <summary>성과·비용 상세 보기</summary>
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
