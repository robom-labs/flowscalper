// 예기치 않은 화면 예외를 빈 페이지 대신 PAPER 안전 복구 안내로 전환한다.
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface AppErrorBoundaryProps {
  children: ReactNode
  reload?: () => void
}

interface AppErrorBoundaryState {
  failed: boolean
}

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { failed: false }

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('FlowScalper 화면 표시 오류', error, info.componentStack)
  }

  private reload = () => {
    if (this.props.reload) {
      this.props.reload()
      return
    }
    window.location.reload()
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <main className="release-mismatch-shell">
        <section className="release-mismatch-card" role="alert" aria-labelledby="app-error-title">
          <p className="eyebrow">PAPER SAFE RECOVERY</p>
          <h1 id="app-error-title">화면을 표시하는 중 문제가 생겼습니다.</h1>
          <p>현재 화면 조작을 멈췄습니다. PAPER 계산만 사용하며 실제 주문은 계속 0입니다.</p>
          <p className="release-mismatch-help">화면을 다시 불러와도 반복되면 설정의 시스템 진단을 확인하세요.</p>
          <button type="button" onClick={this.reload}>화면 다시 불러오기</button>
        </section>
      </main>
    )
  }
}
