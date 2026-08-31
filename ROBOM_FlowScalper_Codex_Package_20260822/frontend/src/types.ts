// 대시보드 API와 React 화면 사이의 타입 계약을 정의한다.
export type PageId =
  | 'market'
  | 'strategies'
  | 'trades'
  | 'settings'

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
  candidate_id?: string | null
  signal_event_id?: string | null
  opportunity_id?: string
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
  mae_r?: string | null
  mfe_r?: string | null
  peak_unrealized_usdt?: string | null
  giveback_usdt?: string | null
  runner_net_pnl_usdt?: string | null
  trail_trigger_slippage_usdt?: string | null
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

export type TradeOpportunity = {
  key: {
    run_id: string
    strategy_id: string
    strategy_version: string
    opportunity_id: string
    symbol: string
    side: string
  }
  family_id: string | null
  family_label_ko: string | null
  variant_label_ko: string | null
  entry_ts_ms: number
  exit_ts_ms: number
  profiles: Partial<Record<'BASE' | 'STRESS', HistoryRow | HistoryRow[]>>
  profile_account_refs?: Partial<Record<'BASE' | 'STRESS', TradeProfileAccountRef>>
  accounts?: TradeAccountResult[]
  account_groups?: TradeAccountGroup[]
  rows: HistoryRow[]
  raw_result_row_count: number
  base_result_row_count: number
  stress_result_row_count: number
  partial_exit_row_count: number
  replay_available: boolean
}

export type TradeProfileAccountRef = {
  account_scope: 'MAIN' | 'LEAGUE'
  account_id: string
}

export type TradeAccountResult = {
  account_scope: 'MAIN' | 'LEAGUE'
  account_id: string
  profiles: Partial<Record<'BASE' | 'STRESS', HistoryRow | HistoryRow[]>>
  rows: HistoryRow[]
  raw_result_row_count: number
  base_result_row_count: number
  stress_result_row_count: number
  partial_exit_row_count: number
}

export type TradeAccountGroup = {
  account_scope: 'MAIN' | 'LEAGUE'
  account_group_id: string
  account_ids: string[]
  profiles: Partial<Record<'BASE' | 'STRESS', HistoryRow | HistoryRow[]>>
  profile_account_refs: Partial<Record<'BASE' | 'STRESS', TradeProfileAccountRef>>
  rows: HistoryRow[]
  raw_result_row_count: number
  base_result_row_count: number
  stress_result_row_count: number
  partial_exit_row_count: number
}

export type TradesResponse = {
  schema_version: 1
  opportunities: TradeOpportunity[]
  counts: {
    unique_opportunities: number
    returned_opportunities?: number
    raw_result_rows: number
    base_result_rows: number
    stress_result_rows: number
    unresolved_result_rows?: number
    source_raw_result_rows?: number
  }
  grouping_status?: 'PROVEN' | 'NOT_PROVEN'
  source_status?: 'COMPLETE' | 'NOT_PROVEN_RAW_LIMIT_BOUNDARY'
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}

export type StrategyRow = {
  strategy_id: string
  family_id?: string
  family_label_ko?: string
  role?: string
  variant_id?: string
  variant_label_ko?: string
  is_current_variant?: boolean
  supersedes_strategy_ids?: string[]
  superseded_by_strategy_id?: string | null
  user_visible_by_default?: boolean
  default_research_enabled?: boolean
  final_ranking_eligible?: boolean
  reason_code?: string | null
  reason_ko?: string | null
  reason_group?: string | null
  blocking?: boolean
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
  max_hold_seconds: number | null
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

export type StrategyPerformanceSummary = Omit<
  StrategyPerformance,
  'metric_status' | 'regime_contributions' | 'windows'
>

export type StrategySummaryRow = Pick<
  StrategyRow,
  | 'strategy_id'
  | 'family_id'
  | 'role'
  | 'variant_id'
  | 'variant_label_ko'
  | 'is_current_variant'
  | 'supersedes_strategy_ids'
  | 'superseded_by_strategy_id'
  | 'user_visible_by_default'
  | 'default_research_enabled'
  | 'final_ranking_eligible'
  | 'reason_code'
  | 'reason_ko'
  | 'reason_group'
  | 'blocking'
  | 'display_name_ko'
  | 'short_name'
  | 'mode'
  | 'lifecycle'
  | 'long_enabled'
  | 'short_enabled'
  | 'strategy_version'
  | 'evaluated_paths'
  | 'qualified_paths'
  | 'latest_status'
  | 'latest_reasons'
  | 'paper_only'
> & {
  family_label_ko?: string
  performance: Record<'BASE' | 'STRESS', StrategyPerformanceSummary>
}

export type StrategyFamilyResearchSetting = Partial<Pick<
  StrategyRow,
  'mode' | 'lifecycle' | 'long_enabled' | 'short_enabled' | 'settings_revision' | 'manual_lock' | 'change_reason'
>> & {
  research_enabled?: boolean
  enabled?: boolean
  revision?: number
}

export type ResearchSourceMetadata = {
  source_id: string
  title: string
  publisher: string
  date: string
  url: string | null
  idea_used: string
  our_modification: string
  metadata_status: 'REGISTERED' | 'NOT_PROVEN'
}

export type StrategyFamilyVariantDetail = {
  strategy_id: string
  family_id?: string
  role?: string
  variant_id?: string
  variant_label_ko?: string
  is_current_variant?: boolean
  supersedes_strategy_ids?: string[]
  superseded_by_strategy_id?: string | null
  user_visible_by_default?: boolean
  default_research_enabled?: boolean
  final_ranking_eligible?: boolean
  setting?: StrategyFamilyResearchSetting
  runtime_state?: UiStrategyStateRow
  research_sources?: ResearchSourceMetadata[]
}

export type StrategyFamilyDetail = {
  family_id: string
  label_ko?: string
  category_ko?: string
  description_ko?: string
  display_order?: number
  current_variant_id?: string | null
  variant_count?: number
  availability_state?: string
  availability_label_ko?: string
  availability_reason_ko?: string
  variants: StrategyFamilyVariantDetail[]
  offline_challengers?: Array<Record<string, unknown>>
  paper_only?: true
  real_orders_enabled?: false
  auth_required?: false
}

export type StrategyFamilyCatalogVariant = Omit<
  StrategyFamilyVariantDetail,
  'setting' | 'runtime_state' | 'research_sources'
>

export type StrategyFamilyCatalogRow = {
  family_id: string
  label_ko: string
  category_ko: string
  description_ko: string
  display_order: number
  current_variant_id: string | null
  variant_count: number
  availability_state: string
  availability_label_ko: string
  availability_reason_ko: string
  variants: StrategyFamilyCatalogVariant[]
}

export type StrategyFamilyCatalogPayload = {
  schema_version: 1
  families: StrategyFamilyCatalogRow[]
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}

export type StrategyFamilyCondition = {
  condition_id: string
  label_ko: string
  threshold_ko: string
  current_value: string | number | boolean | null
  status: string
  reason_ko?: string | null
}

export type OrderflowFilterLatest = {
  symbol?: string
  side?: string
  score?: string | number | null
  passed_component_count?: number
  persistence_ms?: number
  allowed?: boolean
  creates_candidate_plan?: false
  components?: Record<string, string | number | boolean | null>
  data_health?: string
}

export type OrderflowFilterStatus = {
  enabled?: boolean
  research_enabled?: boolean
  revision?: number
  latest_score?: string | number | null
  data_health?: string
  affected_strategy_ids?: string[]
  latest?: OrderflowFilterLatest[]
  uplift_status?: string
  creates_candidate_plan?: boolean
  paper_only?: true
}

export type StrategyFamilyConditionsResponse = {
  schema_version: number
  family_id: string
  strategy_id?: string | null
  symbol?: string | null
  setup_state?: string
  passed: number | null
  total: number
  top_blockers: string[]
  conditions: StrategyFamilyCondition[]
  sides?: Record<string, unknown> | unknown[]
  execution?: {
    side?: string | null
    entry?: string | number | null
    initial_stop?: string | number | null
    take_profit_1?: string | number | null
    take_profit_2?: string | number | null
    TP1?: string | number | null
    TP2?: string | number | null
    trailing_activation?: string | number | null
    current_trail?: string | number | null
    remaining_quantity?: string | number | null
    expiry?: string | number | null
    expiry_ts_ms?: number | null
    expires_at?: string | number | null
  }
  pending_count?: number
  open_count?: number
  open_positions?: Array<Record<string, unknown>>
  research_sources?: ResearchSourceMetadata[]
  filter?: OrderflowFilterStatus | null
  paper_only: true
  real_orders_enabled: false
  auth_required: false
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
  unique_opportunity_count?: number
  raw_ledger_row_count?: number
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
  daily_trade_count: number
  max_daily_trades: number
  realized_today_usdt: string
  realized_week_usdt: string
  daily_period_start_ms: number | null
  weekly_period_start_ms: number | null
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
  strategies: StrategySummaryRow[]
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
  settings_summary?: SettingsSummaryPayload
  diagnostics?: DiagnosticsPayload
  strategy_family_catalog?: StrategyFamilyCatalogPayload
}

export type UiStrategyStateRow = Partial<Omit<StrategySummaryRow, 'strategy_id' | 'performance'>> & {
  strategy_id: string
  performance?: Partial<Record<'BASE' | 'STRESS', Partial<StrategyPerformanceSummary>>>
}

export type StrategyPageSummaryPayload = {
  schema_version: 1
  analysis_scope: 'CURRENT_STRATEGY_VERSION'
  strategies: StrategySummaryRow[]
  league_accounts: LeagueAccount[]
  strategy_count: number
  league_account_count: number
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}

export type SettingsSummaryPayload = {
  schema_version: 1
  run: {
    run_id: string
    mode: SystemStatus['mode']
    venue: SystemStatus['venue']
    new_run_preserves_history: true
  }
  safety: {
    paper_only: true
    real_orders_enabled: false
    auth_required: false
    private_api_enabled: false
    api_key_enabled: false
    wallet_enabled: false
    runtime_ai_order_decision_enabled: false
    entry_state: DashboardData['paper_entry_intent']['state']
    entry_revision: number
    active_locks: string[]
  }
  costs: Partial<DashboardData['risk']['strategy_league']>
  storage: {
    label?: string
    free_mb?: number | null
    free_ratio?: number | null
    entry_allowed?: boolean | null
    lock_reason?: string | null
  }
  connection: { state?: string; public_market_only: true }
  autostart: {
    state: 'NOT_PROVEN' | 'VERIFIED_ENABLED' | 'VERIFIED_DISABLED'
    paper_state_recovery_reported?: boolean | null
    launch_agent_verified: boolean
    read_only: true
    evidence_source: string
    evidence_ko: string
  }
  local_preferences: {
    research_detail_default: boolean
    research_detail_affects_execution: false
  }
  funding_readiness: 'NOT_READY'
}

export type DiagnosticsPayload = {
  schema_version: 1
  rows: {
    key: string
    label_ko: string
    value: string | number | boolean | null
    severity: 'OK' | 'INFO' | 'WARNING' | 'CRITICAL'
    user_visible: boolean
    group: string
  }[]
  raw: Record<string, string | number | boolean>
  paper_only: true
  real_orders_enabled: false
  auth_required: false
}

export type UiSummaryPayload = Partial<Omit<DashboardData, 'strategies'>> & {
  schema_version?: 1
  strategy_state?: UiStrategyStateRow[]
  paper_only?: true
  real_orders_enabled?: false
  auth_required?: false
}

export type UiStrategyRowDelta = {
  rows: UiStrategyStateRow[]
  removed_strategy_ids: string[]
}

export type UiChartDelta = {
  symbol: string
  interval: string
  fixture: boolean
  refresh_required: boolean
  point_upserts?: ChartPoint[]
  removed_point_ts_ms?: number[]
  candle_upserts?: ChartData['candles']
  removed_candle_open_ts_ms?: number[]
  lines?: ChartData['lines']
}

export type UiSelectedFamilyDetail = StrategyFamilyDetail

export type UiSelectedDetailDelta = {
  family_id: string | null
  detail: UiSelectedFamilyDetail | null
}

type UiWebSocketEnvelope<TType extends string, TData> = {
  schema_version: 1
  sequence: number
  type: TType
  data: TData
}

export type UiWebSocketServerMessage =
  | UiWebSocketEnvelope<'snapshot', UiSummaryPayload>
  | UiWebSocketEnvelope<'summary_delta', UiSummaryPayload>
  | UiWebSocketEnvelope<'chart_delta', UiChartDelta>
  | UiWebSocketEnvelope<'position_delta', UiSummaryPayload>
  | UiWebSocketEnvelope<'strategy_row_delta', UiStrategyRowDelta>
  | UiWebSocketEnvelope<'selected_detail_delta', UiSelectedDetailDelta>
  | UiWebSocketEnvelope<'heartbeat', { server_ts_ms: number }>
  | UiWebSocketEnvelope<'error', { error_code: string; error_message_ko: string; retryable: boolean }>

export type UiWebSocketClientMessage =
  | { type: 'select_family'; family_id: string | null }
  | { type: 'ping' }

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
