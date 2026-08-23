// performance.now와 requestAnimationFrame으로 속도와 무관한 결정적 가상시간을 제공한다.
export type ReplayClockFrame = { ts_ms: number }

type Now = () => number
type RequestFrame = (callback: FrameRequestCallback) => number
type CancelFrame = (handle: number) => void

export class ReplayClock<T extends ReplayClockFrame> {
  private index = 0
  private speed = 5
  private playing = false
  private anchorRealMs = 0
  private anchorVirtualMs = 0
  private frameHandle = 0

  constructor(
    private readonly frames: readonly T[],
    private readonly onFrame: (frame: T, index: number, completed: boolean) => void,
    private readonly now: Now = () => performance.now(),
    private readonly requestFrame: RequestFrame = (callback) => requestAnimationFrame(callback),
    private readonly cancelFrame: CancelFrame = (handle) => cancelAnimationFrame(handle),
  ) {}

  play() {
    if (this.playing || !this.frames.length) return
    this.playing = true
    this.anchorRealMs = this.now()
    this.anchorVirtualMs = this.frames[this.index].ts_ms
    this.frameHandle = this.requestFrame(this.tick)
  }

  pause() {
    if (!this.playing) return
    this.playing = false
    this.cancelFrame(this.frameHandle)
  }

  setSpeed(speed: number) {
    if (![0.5, 1, 2, 5, 10, 20, 40, 80].includes(speed)) throw new Error('지원하지 않는 리플레이 속도입니다.')
    const wasPlaying = this.playing
    this.pause()
    this.speed = speed
    if (wasPlaying) this.play()
  }

  seek(index: number) {
    this.pause()
    this.index = Math.max(0, Math.min(index, this.frames.length - 1))
    const frame = this.frames[this.index]
    if (frame) this.onFrame(frame, this.index, this.index === this.frames.length - 1)
  }

  step() {
    this.seek(this.index + 1)
  }

  dispose() {
    this.pause()
  }

  private tick = () => {
    if (!this.playing || !this.frames.length) return
    const target = this.anchorVirtualMs + (this.now() - this.anchorRealMs) * this.speed
    let emitted = false
    while (this.index + 1 < this.frames.length && this.frames[this.index + 1].ts_ms <= target) {
      this.index += 1
      this.onFrame(this.frames[this.index], this.index, this.index === this.frames.length - 1)
      emitted = true
    }
    const completed = this.index === this.frames.length - 1
    if (!emitted) this.onFrame(this.frames[this.index], this.index, completed)
    if (completed) {
      this.playing = false
      return
    }
    this.frameHandle = this.requestFrame(this.tick)
  }
}
