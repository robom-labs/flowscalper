// 사용자가 프로그램의 최근 판단과 동작을 시간순으로 확인하게 한다.
import type { LogItem } from '../types'
import { formatKstDateTime, formatKstTime } from '../time'

type Props = { logs: LogItem[] }

export function EventLog({ logs }: Props) {
  return (
    <section className="panel log-panel" aria-labelledby="log-title">
      <div className="panel-title"><h2 id="log-title">최근 활동</h2><span>{logs.length}건</span></div>
      <div className="log-list">
        {logs.map((log, index) => (
          <article key={`${log.ts_ms}-${index}`}>
            <time dateTime={new Date(log.ts_ms).toISOString()} title={formatKstDateTime(log.ts_ms)}>
              {formatKstTime(log.ts_ms)}
            </time>
            <span className="log-category">{log.category}</span>
            <p>{log.message}</p>
          </article>
        ))}
        {logs.length === 0 ? <p className="empty-copy">새로운 활동을 기다리고 있습니다.</p> : null}
      </div>
    </section>
  )
}
