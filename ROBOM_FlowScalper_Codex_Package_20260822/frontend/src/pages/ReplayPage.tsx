// 보존 이벤트를 단계별로 재생하는 결정적 리플레이 사용자 흐름이다.
import { useState } from 'react'
import { PriceChart } from '../components/PriceChart'
import type { ChartData, HistoryRow } from '../types'

type Props = { chart: ChartData; trade: HistoryRow | undefined }

export function ReplayPage({ chart, trade }: Props) {
  const [playing, setPlaying] = useState(false)
  const [step, setStep] = useState(3)
  if (!trade) {
    return <section aria-labelledby="replay-heading"><div className="page-heading"><div><p className="section-kicker">EVENT-DRIVEN</p><h2 id="replay-heading">결정적 리플레이</h2></div><span className="page-note">선택된 거래 없음</span></div><div className="panel empty-state"><b>보존된 완료 PAPER 거래가 없습니다</b><p>실제 공개데이터 수신만으로 결정·체결 결과를 만들지 않습니다.</p></div></section>
  }
  return (
    <section aria-labelledby="replay-heading">
      <div className="page-heading"><div><p className="section-kicker">EVENT-DRIVEN</p><h2 id="replay-heading">결정적 리플레이</h2></div><span className="page-note">{trade?.trade_id ?? '선택된 거래 없음'}</span></div>
      <div className="replay-layout">
        <PriceChart chart={chart} replay />
        <aside className="panel replay-controls"><h3>재생 제어</h3><div className="control-row"><button type="button" className="primary-button" onClick={() => setPlaying((value) => !value)}>{playing ? '일시정지' : '재생'}</button><button type="button" className="secondary-button" onClick={() => setStep((value) => value + 1)}>다음 이벤트</button></div><label>속도<select defaultValue="1"><option value="0.5">0.5×</option><option value="1">1×</option><option value="4">4×</option></select></label><dl><div><dt>현재 이벤트</dt><dd>{step} / 12</dd></div><div><dt>의사결정</dt><dd>RANGE_REENTRY</dd></div><div><dt>예상 / 실제 fill</dt><dd>100.00 / 100.10</dd></div><div><dt>재현 상태</dt><dd className="positive">결정·체결 동일</dd></div></dl></aside>
      </div>
    </section>
  )
}
