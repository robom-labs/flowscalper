// 비전문가가 현재 PAPER 관찰과 거래 수를 한눈에 확인하는 짧은 요약 화면이다.
import type { DashboardData, PageId } from '../types'
import { formatUsdt } from '../format'

type Props = { data: DashboardData; onNavigate: (page: PageId) => void }

export function LivePage({ data, onNavigate }: Props) {
  const open = data.league_accounts.filter((row) => row.profile === 'BASE').reduce((sum, row) => sum + row.open_positions, 0)
  return <section aria-labelledby="summary-heading"><div className="page-heading"><div><p className="section-kicker">한눈에 보기</p><h2 id="summary-heading">프로그램 요약</h2><p className="heading-help">실제 주문 없이 공개시장 데이터를 보며 PAPER 결과만 기록합니다.</p></div><button type="button" className="primary-button" onClick={() => onNavigate('terminal')}>시장 화면 열기</button></div><section className="metric-strip"><article><span>현재 자산</span><b>{formatUsdt(data.status.current_equity_usdt, { equity: true })}</b></article><article><span>진행 중</span><b>{open}건</b></article><article><span>완료 거래</span><b>{data.status.trade_count}건</b></article><article><span>정밀 분석</span><b>{data.status.deep_symbols || data.scanner.length}종목</b></article><article><span>실제 주문</span><b>0건</b></article></section><section className="panel shared-benchmark-card"><div><p className="section-kicker">통합 가상계좌</p><h3>1,000 USDT PAPER 기준</h3><p>전략별 가상계좌와 분리해 보는 통합 기준입니다.</p></div><dl><div><dt>시작자산</dt><dd>{formatUsdt(data.status.starting_equity_usdt, { equity: true })}</dd></div><div><dt>순손익</dt><dd>{formatUsdt(data.status.realized_pnl_usdt, { signed: true })}</dd></div><div><dt>수수료</dt><dd>{formatUsdt(data.status.cumulative_fees_usdt)}</dd></div><div><dt>슬리피지</dt><dd>{formatUsdt(data.status.cumulative_slippage_usdt)}</dd></div></dl></section></section>
}
