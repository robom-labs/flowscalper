// Strategy League 화면 단위검사에 쓰는 10전략·20계좌 결정적 fixture를 제공한다.
import { initialDashboard } from '../src/demoData'
import { strategyOrder } from '../src/strategyPresentation'
import type { DashboardData, LeagueAccount, StrategyPerformance, StrategyRow } from '../src/types'

const names = [
  ['LSA 반전', '급락·급등 쓸기 반전'],
  ['CBR 돌파', '압축 돌파 재가속'],
  ['VWAP 소진', 'VWAP 과도이탈 평균복귀'],
  ['OFI 눌림', 'OFI 추세 눌림 지속'],
  ['호가 쏠림', '호가 쏠림 순간추세'],
  ['체결흐름', '강한 체결 흐름 지속'],
  ['다중호가', '다중호가 공정가 추세'],
  ['깊이 OFI', '깊이보정 OFI 충격'],
  ['OFI·가격동행', 'OFI·단기수익률 동행'],
  ['호가 기울기', '호가 기울기 비대칭'],
] as const

function performance(strategyId: string, profile: 'BASE' | 'STRESS'): StrategyPerformance {
  return {
    strategy_id: strategyId,
    profile,
    sample_size: 0,
    wins: 0,
    losses: 0,
    breakevens: 0,
    win_rate: null,
    win_rate_ci95: null,
    average_win_usdt: null,
    average_loss_usdt: null,
    payoff_ratio: null,
    expectancy_usdt: null,
    expectancy_r: null,
    expectancy_bps: null,
    profit_factor: null,
    gross_pnl: '0',
    fees: '0',
    slippage: '0',
    net_pnl: '0',
    cost_burden: null,
    maximum_drawdown: '0',
    mae_r_mean: null,
    mfe_r_mean: null,
    median_hold_ms: null,
    p90_hold_ms: null,
    sample_status: '표본 부족',
    sample_span_days: 0,
    regime_count: 0,
    regimes: [],
    symbols: [],
    sides: { LONG: 0, SHORT: 0 },
    stress_verified: profile === 'STRESS',
    recommendation: '표본 수집',
    recommendation_is_advisory: true,
    analysis_scope: 'CURRENT_STRATEGY_VERSION',
    strategy_version: 'fixture-current',
    excluded_prior_version_samples: 0,
    windows: { recent_50: { sample_size: 0 }, recent_100: { sample_size: 0 }, recent_300: { sample_size: 0 } },
  }
}

export const strategies: StrategyRow[] = strategyOrder.map((strategyId, index) => ({
  strategy_id: strategyId,
  short_name: names[index][0],
  display_name_ko: names[index][1],
  summary_ko: '공개시장 구조와 체결 흐름을 PAPER로만 평가합니다.',
  stability: index === 1 ? 'STABLE' : 'EXPERIMENTAL',
  supported_regimes: ['RANGE'],
  paper_only: true,
  mode: index === 1 ? 'ACTIVE' : [0, 3, 4, 7].includes(index) ? 'OFF' : 'SHADOW',
  long_enabled: true,
  short_enabled: true,
  evaluated_paths: 0,
  qualified_paths: 0,
  latest_status: 'WAITING_DATA',
  latest_reasons: [],
  performance: {
    BASE: performance(strategyId, 'BASE'),
    STRESS: performance(strategyId, 'STRESS'),
  },
}))

export const leagueAccounts: LeagueAccount[] = strategies.flatMap((strategy, strategyIndex) => (
  (['BASE', 'STRESS'] as const).map((profile) => ({
    account_id: `${strategy.strategy_id}:${profile}`,
    strategy_id: strategy.strategy_id,
    profile,
    starting_equity_usdt: '1000',
    current_equity_usdt: profile === 'BASE' ? String(1000 + strategyIndex) : '1000',
    realized_pnl_usdt: '0',
    unrealized_pnl_usdt: '0',
    fees_usdt: '0',
    slippage_usdt: '0',
    trade_count: 0,
    wins: 0,
    losses: 0,
    win_rate: null,
    open_positions: 0,
    pending_entries: 0,
    gross_notional_usdt: '0',
    effective_leverage: '0',
    maximum_effective_leverage: '5',
    maximum_drawdown_usdt: '0',
    paused: false,
    faulted: false,
  }))
))

export function dashboardFixture(): DashboardData {
  return {
    ...initialDashboard,
    status: { ...initialDashboard.status },
    chart: { ...initialDashboard.chart, points: [], candles: [], lines: { ...initialDashboard.chart.lines } },
    strategies: strategies.map((strategy) => ({ ...strategy })),
    league_accounts: leagueAccounts.map((account) => ({ ...account })),
  }
}
