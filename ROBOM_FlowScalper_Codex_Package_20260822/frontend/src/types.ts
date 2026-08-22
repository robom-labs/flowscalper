// 대시보드 API와 React 화면 사이의 타입 계약을 정의한다.
export type PageId = 'live' | 'history' | 'replay' | 'performance' | 'risk' | 'system'

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
  lines: { entry: number | null; take_profit: number | null; stop: number | null }
  fixture: boolean
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
  initial_stop: string
  quantity: string
  notional: string
  risk_budget: string
  maximum_planned_loss: string
  gross_pnl: string
  net_pnl: string
  fees: string
  slippage: string
  elapsed_seconds: number
  expected_resolution: string
  health: Record<string, number>
  management_reason: string
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
  exit_reason: string
  gross_pnl: string
  fees: string
  slippage: string
  net_pnl: string
  holding_seconds: number
  profile: string
  sample_type: string
}

export type DashboardData = {
  status: SystemStatus
  paused: boolean
  scanner: ScannerRow[]
  chart: ChartData
  position: CurrentPosition | null
  logs: LogItem[]
  history: HistoryRow[]
  performance: Record<string, string | number>
  risk: {
    risk_per_trade: string
    max_positions: number
    daily_loss_limit: string
    weekly_loss_limit: string
    drawdown_lock: string
    active_locks: string[]
    immutable_run: boolean
  }
  system: Record<string, string | number | boolean>
}
