// 대시보드 API와 React 화면 사이의 타입 계약을 정의한다.
export type PageId =
  | 'summary'
  | 'strategies'
  | 'positions'
  | 'history'
  | 'replay'
  | 'performance'
  | 'strategy-symbol'
  | 'risk'
  | 'terminal'
  | 'system'

export type ControlAction = 'START_LIVE' | 'START_DEMO' | 'NEW_RUN'

export type ControlState =
  | 'REQUESTED'
  | 'PREPARING'
  | 'CONNECTING_PRIMARY'
  | 'CONNECTING_FALLBACK'
  | 'COMPLETED'
  | 'FAILED_RETRYABLE'
  | 'FAILED_BLOCKED'
  | 'CANCELLING'
  | 'CANCELLED'

export type ControlOperation = {
  operation_id: string
  action: ControlAction
  state: ControlState
  stage_ko: string
  started_ts_ms: number
  updated_ts_ms: number
  finished_ts_ms: number | null
  retryable: boolean
  error_code: string | null
  error_message_ko: string | null
  idempotency_key: string | null
  revision: number
  actor: string
  reason: string
  history: { state: ControlState; stage_ko: string; ts_ms: number }[]
}

export type SystemStatus = {
  mode: 'READY' | 'LIVE_SHADOW_PAPER' | 'DEMO_FIXTURE' | 'REPLAY'
  market_data_state: 'LIVE' | 'RECONNECTING' | 'STALE' | 'DISCONNECTED' | 'FIXTURE'
  execution_state: 'PAPER'
  venue: 'NONE' | 'BINANCE_USDM' | 'BYBIT_LINEAR' | 'FIXTURE'
  run_id: string
  starting_equity_usdt: number
  current_equity_usdt: number
  realized_pnl_usdt: number
  unrealized_pnl_usdt: number
  cumulative_fees_usdt: number
  cumulative_slippage_usdt: number
  trade_count: number
  real_orders_enabled: false
  auth_required: false
  wide_symbols: number
  deep_symbols: number
  processing_lag_p95_ms: number | null
  health_flags: string[]
}

export type ScannerRow = {
  rank: number
  symbol: string
  depth: 'WIDE' | 'DEEP'
  regime: string
  strategy: string
  side: 'LONG' | 'SHORT' | 'NONE'
  score: number | null
  net_rr: number | null
  expected_cost_bps: number
  spread_bps: number
  data_health: string
  status: string
  reason: string
  reason_codes?: string[]
  calibration: 'CALIBRATING'
}

export type ChartPoint = {
  index: number
  ts_ms: number
  bid: number
  ask: number
  mid: number
  microprice: number
}

export type ChartData = {
  symbol: string
  interval: string
  points: ChartPoint[]
  candles: {
    time: number
    open_ts_ms: number
    open: number
    high: number
    low: number
    close: number
    volume: number
    trade_count: number
  }[]
  lines: {
    entry: number | null
    take_profit: number | null
    take_profit_2?: number | null
    stop: number | null
  }
  fixture: boolean
}

export type TimeframeOption = {
  interval_seconds: number
  label: string
  label_ko: string
}

export type CurrentPosition = {
  symbol: string
  venue: string
  side: 'LONG' | 'SHORT'
  strategy: string
  signal_time: number
  planned_entry: string
  actual_entry: string
  take_profit: string
  take_profit_1?: string
  take_profit_2?: string | null
  initial_stop: string
  current_stop?: string
  quantity: string
  remaining_quantity?: string
  notional: string
  risk_budget: string
  maximum_planned_loss: string
  gross_pnl: string
  net_pnl: string
  fees: string
  slippage: string
  elapsed_seconds: number
  expected_resolution?: string
  health?: Record<string, number>
  management_reason: string
  management_policy?: string[]
}

export type LogItem = {
  ts_ms: number
  category: string
  level: string
  message: string
}

export type HistoryRow = {
  run_id: string
  trade_id: string
  symbol: string
  strategy: string
  side: string
  entry: string
  exit: string
  entry_ts_ms: number
  exit_ts_ms: number
  initial_stop: string
  take_profit: string
  take_profit_1?: string | null
  take_profit_2?: string | null
  tp1_hit_ts_ms?: number | null
  tp2_hit_ts_ms?: number | null
  time_to_tp1_ms?: number | null
  time_to_tp2_ms?: number | null
  time_to_stop_ms?: number | null
  trailing_activation_ts_ms?: number | null
  runner_started_ts_ms?: number | null
  peak_unrealized_usdt?: string
  giveback_usdt?: string
  runner_net_pnl_usdt?: string
  trail_trigger_slippage_usdt?: string
  trailing_state_checksum?: string | null
  quantity: string
  exit_reason: string
  gross_pnl: string
  fees: string
  slippage: string
  net_pnl: string
  holding_ms: number
  holding_seconds: number
  profile: string
  sample_type: string
  account_scope?: 'MAIN' | 'LEAGUE'
  account_id?: string
  strategy_version?: string
  config_hash?: string
  replay_available?: boolean
}

export type HistoryResponse = {
  rows: HistoryRow[]
  scope: {
    run_scope: 'CURRENT' | 'ALL'
    account_scope: 'MAIN' | 'LEAGUE' | 'ALL'
    profile: 'BASE' | 'STRESS' | 'ALL'
    version_scope: 'CURRENT' | 'ALL'
    sample_type: 'LIVE_PUBLIC' | 'OFFLINE_FIXTURE' | 'ALL'
    strategy_version: string
    returned_count: number
    limit: number
  }
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}

export type StrategyRow = {
  strategy_id: string
  display_name_ko: string
  short_name: string
  summary_ko: string
  stability: 'STABLE' | 'EXPERIMENTAL'
  supported_regimes: string[]
  exit_style: string
  horizon_class: 'MICRO_SCALP' | 'FAST_INTRADAY' | 'INTRADAY_SWING'
  expected_holding_seconds: [number, number]
  signal_half_life_seconds: number
  required_timeframes: string[]
  exit_model: string
  take_profit_1_r: string
  take_profit_2_r: string
  entry_rules_ko: string[]
  exit_rules_ko: string[]
  max_hold_seconds: number
  cost_model_version: string
  strategy_version: string
  required_market_data: string[]
  minimum_warmup_ko: string
  entry_hypothesis_ko: string
  falsification_conditions_ko: string[]
  edge_decay_policy_ko: string
  risk_budget_rule_ko: string
  target_universe_ko: string
  data_leakage_guards_ko: string[]
  research_source_ids: string[]
  paper_only: true
  mode: 'ACTIVE' | 'SHADOW' | 'OFF'
  lifecycle: 'RESEARCH' | 'SHADOW' | 'CHALLENGER' | 'ACTIVE' | 'QUARANTINED' | 'RETIRED'
  long_enabled: boolean
  short_enabled: boolean
  settings_revision: number
  manual_lock: boolean
  changed_by: 'USER_UI' | 'AUTO_GOVERNOR' | 'RECOVERY' | 'MIGRATION'
  change_reason: string
  settings_updated_ts_ms: number
  policy_reactivation_locked: boolean
  evaluated_paths: number
  qualified_paths: number
  latest_status: string
  latest_reasons: string[]
  performance: Record<'BASE' | 'STRESS', StrategyPerformance>
  governance: StrategyGovernance
}

export type StrategyGovernance = {
  strategy_id: string
  current_lifecycle: StrategyRow['lifecycle']
  recommended_lifecycle: StrategyRow['lifecycle']
  reason_codes: string[]
  automatic_action_allowed: boolean
  transition_required: boolean
  champion_id: string | null
  last_evaluated_ts_ms: number
  evaluation_period: string
  evidence_status: 'NOT_PROVEN' | 'PROVEN'
  remaining_live_samples: number
  remaining_days: number
  manual_lock: boolean
  settings_revision: number
  change_history: Array<{
    strategy_id: string
    mode: StrategyRow['mode']
    lifecycle: StrategyRow['lifecycle']
    long_enabled: boolean
    short_enabled: boolean
    settings_revision: number
    manual_lock: boolean
    changed_by: StrategyRow['changed_by']
    change_reason: string
    settings_updated_ts_ms: number
    policy_reactivation_locked: boolean
    transition_id: string
    previous_state: string
    new_state: string
    occurred_ts_ms: number
    cause: string
    cause_code: string
    description_ko: string
    actor: 'USER_UI' | 'AUTO_SAFETY' | 'AUTO_GOVERNOR' | 'RECOVERY' | 'CODEX_DEPLOY'
    run_id: string
    account_id: null
    symbol: null
    request_revision: number
    response_revision: number
    reversible: boolean
  }>
}

export type StrategyPerformance = {
  strategy_id: string
  profile: 'BASE' | 'STRESS'
  sample_size: number
  wins: number
  losses: number
  breakevens: number
  win_rate: string | null
  win_rate_ci95: { lower: string; upper: string } | null
  average_win_usdt: string | null
  average_loss_usdt: string | null
  payoff_ratio: string | null
  expectancy_usdt: string | null
  expectancy_r: string | null
  expectancy_bps: string | null
  profit_factor: string | null
  omega_ratio: string | null
  sortino_ratio_per_trade: string | null
  calmar_ratio_nonannualized: string | null
  downside_deviation_usdt: string | null
  gross_pnl: string
  fees: string
  slippage: string
  net_pnl: string
  cost_burden: string | null
  maximum_drawdown: string
  turnover_usdt: string
  turnover_ratio: string
  mae_r_mean: string | null
  mfe_r_mean: string | null
  median_hold_ms: number | null
  p90_hold_ms: number | null
  tp1_sample_size: number
  tp2_sample_size: number
  stop_sample_size: number
  median_time_to_tp1_ms: number | null
  median_time_to_tp2_ms: number | null
  median_time_to_stop_ms: number | null
  trail_activation_count: number
  trail_activation_rate: string | null
  tp1_fill_rate: string | null
  runner_count: number
  runner_rate: string | null
  runner_net_contribution_usdt: string
  mfe_capture_ratio_mean: string | null
  average_peak_giveback_usdt: string
  median_peak_giveback_usdt: string
  p90_peak_giveback_usdt: string
  trailing_exit_count: number
  stop_before_trail_activation_count: number
  activation_after_net_negative_exit_count: number
  trail_trigger_slippage_usdt: string
  regime_contributions: { regime: string; sample_size: number; net_pnl: string; expectancy_usdt: string }[]
  metric_status: Record<string, string>
  sample_status: string
  sample_span_days: number
  regime_count: number
  regimes: string[]
  symbols: string[]
  sides: Record<'LONG' | 'SHORT', number>
  stress_verified: boolean
  recommendation: string
  recommendation_is_advisory: true
  analysis_scope: 'CURRENT_STRATEGY_VERSION'
  strategy_version: string
  data_state?: 'READY' | 'LOADING_HISTORY' | 'HISTORY_UNAVAILABLE'
  excluded_prior_version_samples: number
  windows: Record<string, Record<string, unknown>>
}

export type ShadowAccount = {
  strategy_id: string
  profile: 'BASE' | 'STRESS'
  starting_equity_usdt: string
  current_equity_usdt: string
  realized_pnl_usdt: string
  fees_usdt: string
  slippage_usdt: string
  maximum_drawdown_usdt: string
  closed_trades: number
  open_position: string | null
}

export type LeagueAccount = {
  account_id: string
  strategy_id: string
  profile: 'BASE' | 'STRESS'
  starting_equity_usdt: string
  current_equity_usdt: string
  realized_pnl_usdt: string
  unrealized_pnl_usdt: string
  fees_usdt: string
  slippage_usdt: string
  trade_count: number
  wins: number
  losses: number
  win_rate: string | null
  open_positions: number
  pending_entries: number
  gross_notional_usdt: string
  effective_leverage: string
  maximum_effective_leverage: string
  maximum_drawdown_usdt: string
  paused: boolean
  faulted: boolean
}

export type LeaguePosition = {
  trade_id: string
  candidate_id: string
  account_id: string
  strategy_id: string
  profile: 'BASE' | 'STRESS'
  symbol: string
  side: 'LONG' | 'SHORT'
  signal_time: number
  opened_ts_ms: number
  actual_entry: string
  current_mark: string
  initial_stop: string
  current_stop: string
  TP1: string
  TP2: string
  original_quantity: string
  remaining_quantity: string
  notional: string
  effective_leverage: string
  gross_pnl: string
  fees: string
  slippage: string
  net_pnl: string
  elapsed_seconds: number
  exit_style: string
  management_reason: string
  trailing?: TrailingPositionState
}

export type TrailingPositionState = {
  enabled: boolean
  state: string
  policy_id: string | null
  model?: string | null
  activation_rule?: string
  activation_price: string | null
  activation_ts_ms: number | null
  current_trail: string | null
  previous_trail?: string | null
  runner_quantity: string
  giveback_usdt: string
  data_health?: string
  adverse_active?: boolean
  adverse_reasons?: string[]
  reference_ts_ms?: number | null
  reference_interval_seconds?: number | null
}

export type FocusPosition = {
  focus_key: string
  trade_id: string
  candidate_id: string
  run_id?: string
  account_id: string
  profile: 'BASE' | 'STRESS'
  venue: string
  symbol: string
  side: 'LONG' | 'SHORT'
  strategy: string
  strategy_id: string
  strategy_display_name_ko: string
  exit_style: string
  signal_time: number
  signal_ts_ms: number
  opened_ts_ms?: number
  planned_entry: string
  actual_entry: string
  current_mark: string
  initial_stop: string
  current_stop: string
  take_profit: string
  take_profit_1: string
  take_profit_2: string | null
  quantity: string
  original_quantity: string
  remaining_quantity: string
  notional: string
  notional_usdt: string
  margin_usdt: string
  margin_used_usdt: string
  risk_budget: string
  risk_budget_usdt: string
  maximum_planned_loss: string
  maximum_planned_loss_usdt: string
  remaining_planned_loss_usdt: string
  effective_leverage: string
  gross_pnl: string
  gross_pnl_usdt: string
  fees: string
  entry_fee_usdt: string
  realized_exit_fees_usdt: string
  estimated_exit_fee_usdt: string
  slippage: string
  slippage_usdt: string
  net_pnl: string
  net_pnl_usdt: string
  return_on_margin_pct: string
  account_starting_equity_usdt: string
  account_current_equity_usdt: string
  elapsed_seconds: number
  management_reason: string
  management_reason_ko: string
  stage: string
  stage_ko: string
  data_health: string
  recovered: boolean
  auto_focus_eligible: boolean
  paper_only: true
  real_orders_enabled: false
  auth_required: false
  funding_usdt?: string
  trailing?: TrailingPositionState
}

export type MarketCatalogRow = {
  venue: 'BINANCE_USDM' | 'UPBIT_KRW'
  symbol: string
  display_symbol: string
  base_asset: string
  quote_asset: string
  market_role: 'PAPER_EXECUTION' | 'OBSERVATION_ONLY'
  last: number
  bid: number
  ask: number
  change_percent: number
  quote_volume_24h: number
  trade_count_24h: number
  status: string
  korean_name?: string
  english_name?: string
  strategy_eligible?: boolean
}

export type MarketCatalog = {
  source?: 'ALL_PUBLIC' | 'BINANCE_USDM' | 'UPBIT_KRW'
  count?: number
  rows: MarketCatalogRow[]
  counts: { BINANCE_USDM: number; UPBIT_KRW: number; total: number }
  paper_execution_venue: 'BINANCE_USDM'
  observation_only_venues: ['UPBIT_KRW']
  auth_required: false
  real_orders_enabled: false
}

export type StrategySymbolResponse = {
  generated_ts_ms: number
  rows: StrategySymbolPerformance[]
  ranking_rule: string
  analysis_scope: 'CURRENT_STRATEGY_VERSION'
  strategy_version: string
  excluded_prior_version_samples: number
  real_orders_enabled: false
  auth_required: false
}

export type StrategySymbolPerformance = {
  strategy_id: string
  profile: 'BASE' | 'STRESS'
  symbol: string
  sample_size: number
  sample_status: string
  ranking_eligible: boolean
  rank_score: number | null
  rank: number | null
  win_rate: string | null
  expectancy_usdt: string | null
  profit_factor: string | null
  fees: string
  slippage: string
  net_pnl: string
  maximum_drawdown: string
  analysis_scope: 'CURRENT_STRATEGY_VERSION'
  strategy_version: string
  excluded_prior_version_samples: number
}

export type DashboardData = {
  status: SystemStatus
  paused: boolean
  operation_status: OperationStatus
  paper_entry_intent: {
    state: 'ENTRY_ENABLED' | 'USER_PAUSED'
    manual_pause_requested: boolean
    revision: number
    actor: string
    reason: string
    updated_ts_ms: number | null
    reversible: true
  }
  scanner: ScannerRow[]
  chart: ChartData
  timeframes: TimeframeOption[]
  position: CurrentPosition | null
  logs: LogItem[]
  history: HistoryRow[]
  history_scope: {
    analysis_scope: 'CURRENT_STRATEGY_VERSION'
    strategy_version: string
    excluded_prior_version_samples: number
  }
  strategies: StrategyRow[]
  shadow_accounts: ShadowAccount[]
  league_accounts: LeagueAccount[]
  league_positions: LeaguePosition[]
  focus_positions: FocusPosition[]
  control_operation: ControlOperation | null
  control_revision: number
  performance: Record<string, string | number>
  risk: {
    paper_only: true
    active_locks: string[]
    immutable_run: boolean
    shared_capital: {
      starting_equity_usdt: string
      risk_per_position: string
      max_positions: number
      daily_loss_limit: string
      weekly_loss_limit: string
      drawdown_lock: string
    }
    strategy_league: {
      account_count: number
      starting_equity_per_account_usdt: string
      risk_per_position: string
      max_positions_per_account: number
      maximum_total_open_risk: string
      maximum_effective_leverage: string
      maximum_depth_fraction: string
      daily_loss_limit: string
      weekly_loss_limit: string
      drawdown_lock: string
      base_entry_fee: string
      base_exit_fee: string
      stress_entry_fee: string
      stress_exit_fee: string
    }
  }
  system: Record<string, string | number | boolean>
}

export type OperationStatus = {
  state: 'READY' | 'RUNNING' | 'SAFETY_WAITING' | 'SAFETY_BLOCKED' | 'MANUALLY_PAUSED' | 'RECONNECTING' | 'DEMO_RUNNING' | 'DEMO_PAUSED' | 'REPLAY_RUNNING' | 'REPLAY_PAUSED'
  title_ko: string
  detail_ko: string
  market_observation_active: boolean
  paper_entry_active: boolean
  automatic_recovery: boolean
  recommended_action: 'START' | 'PAUSE' | 'RESUME' | 'NONE'
  lag_p95_ms: number | null
}

export type ReplayRun = {
  run_id: string
  mode: string
  venue: string
  started_ts_ms: number
  finalized_ts_ms: number | null
  market_event_count: number | null
  events_saved: boolean
  trade_count: number
  shadow_trade_count: number
}

export type ReplayResult = {
  replay_id: string
  source_run_id: string
  scope_symbol?: string | null
  created_ts_ms: number
  checksum: string
  input_checksum?: string
  event_count: number
  first_ts_ms: number | null
  last_ts_ms: number | null
  event_type_counts: Record<string, number>
  symbol_counts: Record<string, number>
  strategy_evaluation_count: number
  qualified_signal_count: number
  candidate_plan_count: number
  main_trade_count: number
  shadow_trade_count: number
  decision_path: string[]
  final_state: string
  real_orders_enabled: false
  auth_required: false
}

export type ReplayOperation = {
  operation_id: string
  source_run_id: string
  symbol: string | null
  total_events: number | null
  state: 'REQUESTED' | 'PREPARING' | 'PROCESSING' | 'COMPLETED' | 'FAILED_RETRYABLE' | 'FAILED_BLOCKED' | 'CANCELLING' | 'CANCELLED'
  stage_ko: string
  started_ts_ms: number
  updated_ts_ms: number
  finished_ts_ms: number | null
  retryable: boolean
  error_code: string | null
  error_message_ko: string | null
  result: ReplayResult | null
  revision: number
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}

export type ReplayMarketEvent = {
  event_id: string
  symbol: string
  event_type: string
  venue_ts_ms: number
  data: Record<string, string | number | boolean | unknown[]>
}

export type ReplayTimeline = {
  run_id: string
  symbol: string | null
  total_events: number | null
  truncated: boolean
  available_symbols: { symbol: string; event_count: number | null; new_event_count?: number }[]
  events: ReplayMarketEvent[]
  candles: ChartData['candles']
  preview_only?: boolean
}

export type ReplayFocusFrame = {
  ts_ms: number
  event_id: string
  event_type: string
  data: Record<string, unknown>
  phase: 'PRE_ENTRY' | 'OPEN' | 'CLOSED'
  markers: {
    kind: 'SIGNAL' | 'ENTRY' | 'TP1_HIT' | 'TP2_HIT' | 'STOP_HIT' | 'EXIT'
    ts_ms: number
    price: string
    label?: string
  }[]
  fills: Record<string, unknown>[]
}

export type ReplayFocusSession = {
  session_version: number
  run_id: string
  trade_id: string
  profile: 'BASE' | 'STRESS'
  symbol: string
  side: 'LONG' | 'SHORT'
  strategy_id: string
  levels: {
    signal_ts_ms: number
    entry: string
    initial_stop: string
    take_profit_1: string
    take_profit_2: string | null
  }
  milestones: ReplayFocusFrame['markers']
  start_ts_ms: number
  entry_ts_ms: number
  exit_ts_ms: number
  end_ts_ms: number
  default_speed: 5
  speeds: number[]
  frames: ReplayFocusFrame[]
  candles: ChartData['candles']
  keyframes: { frame_index: number; ts_ms: number }[]
  trade: Record<string, unknown>
  fills: {
    fill_id: string
    trade_id: string
    intent: 'ENTRY' | 'EXIT'
    price: string
    quantity: string
    fee_usdt?: string
    slippage_usdt?: string
    ts_ms: number
    exit_reason?: string
    cost_allocation?: string
  }[]
  profile_comparison: { profile: string; trade_id: string; fees: string; slippage: string; net_pnl: string }[]
  reconciliation: {
    applicable: boolean
    sample_type: string
    matched: boolean | null
    reason: string
    replay_checksum: string
    replay_final_state: string
  }
  checksum: string
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}
