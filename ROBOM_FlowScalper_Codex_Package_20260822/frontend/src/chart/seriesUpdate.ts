// 같은 차트 선택의 최신 캔들은 update하고 과거 변경에서만 전체 reset을 선택한다.
import type { IndicatorCandle } from './indicators'

export type SeriesUpdateMode = 'RESET' | 'UPDATE' | 'NOOP'

function sameCandle(left: IndicatorCandle, right: IndicatorCandle) {
  return left.time === right.time
    && left.open === right.open
    && left.high === right.high
    && left.low === right.low
    && left.close === right.close
    && left.volume === right.volume
}

export function seriesUpdateMode(
  previousSelection: string,
  nextSelection: string,
  previous: IndicatorCandle[],
  next: IndicatorCandle[],
): SeriesUpdateMode {
  if (previousSelection !== nextSelection || next.length < previous.length) return 'RESET'
  if (previous.length === 0) return next.length === 0 ? 'NOOP' : 'RESET'
  if (next.length === 0) return 'RESET'
  const stableCount = Math.max(0, previous.length - 1)
  for (let index = 0; index < stableCount; index += 1) {
    if (!sameCandle(previous[index], next[index])) return 'RESET'
  }
  if (next.length > previous.length + 1) return 'RESET'
  const previousLast = previous.at(-1)
  const nextComparable = next[stableCount]
  if (!previousLast || !nextComparable) return 'RESET'
  if (next.length === previous.length && sameCandle(previousLast, nextComparable)) return 'NOOP'
  if (nextComparable.time < previousLast.time) return 'RESET'
  return 'UPDATE'
}
