// 시장·후보·체결·위험 이벤트를 구조화된 시간순 로그로 표시한다.
import type { LogItem } from '../types'

type Props = { logs: LogItem[] }

export function EventLog({ logs }: Props) {
  return (
    <section className="panel log-panel" aria-labelledby="log-title">
      <div className="panel-title"><h2 id="log-title">이벤트 로그</h2><span>최근 {logs.length}건</span></div>
      <div className="log-list">
        {logs.map((log, index) => (
          <article key={`${log.ts_ms}-${index}`}>
            <time>{new Date(log.ts_ms).toLocaleTimeString('ko-KR')}</time>
            <span className="log-category">{log.category}</span>
            <p>{log.message}</p>
          </article>
        ))}
        {logs.length === 0 ? <p className="empty-copy">이벤트를 기다리는 중입니다.</p> : null}
      </div>
    </section>
  )
}

