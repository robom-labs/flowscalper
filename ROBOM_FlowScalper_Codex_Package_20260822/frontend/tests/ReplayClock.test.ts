// 결정적 가상시간이 재생속도와 탐색 뒤에도 미래 프레임을 먼저 노출하지 않는지 검증한다.
import { describe, expect, it } from 'vitest'
import { ReplayClock } from '../src/replay/ReplayClock'

describe('ReplayClock', () => {
  it('uses performance time and emits the exact final frame at 5x', () => {
    let now = 0
    const callbacks: FrameRequestCallback[] = []
    const emitted: { index: number; completed: boolean }[] = []
    const clock = new ReplayClock(
      [{ ts_ms: 1_000 }, { ts_ms: 1_500 }, { ts_ms: 2_000 }],
      (_frame, index, completed) => emitted.push({ index, completed }),
      () => now,
      (next) => { callbacks.push(next); return callbacks.length },
      () => undefined,
    )

    clock.play()
    now = 100
    callbacks.shift()?.(now)
    expect(emitted.at(-1)).toEqual({ index: 1, completed: false })
    now = 200
    callbacks.shift()?.(now)
    expect(emitted.at(-1)).toEqual({ index: 2, completed: true })
  })

  it('seeks without crossing the requested frame and validates speeds', () => {
    const emitted: number[] = []
    const clock = new ReplayClock(
      [{ ts_ms: 1_000 }, { ts_ms: 2_000 }, { ts_ms: 3_000 }],
      (_frame, index) => emitted.push(index),
    )

    clock.seek(1)
    expect(emitted).toEqual([1])
    expect(() => clock.setSpeed(4)).toThrow('지원하지 않는 리플레이 속도')
    expect(() => clock.setSpeed(80)).not.toThrow()
  })

  it('preserves every ordered key frame when 80x crosses the entire session', () => {
    let now = 0
    const callbacks: FrameRequestCallback[] = []
    const emitted: number[] = []
    const clock = new ReplayClock(
      [{ ts_ms: 0 }, { ts_ms: 100 }, { ts_ms: 200 }, { ts_ms: 300 }, { ts_ms: 400 }],
      (_frame, index) => emitted.push(index),
      () => now,
      (next) => { callbacks.push(next); return callbacks.length },
      () => undefined,
    )
    clock.setSpeed(80)
    clock.play()
    now = 10
    callbacks.shift()?.(now)
    expect(emitted).toEqual([1, 2, 3, 4])
  })
})
