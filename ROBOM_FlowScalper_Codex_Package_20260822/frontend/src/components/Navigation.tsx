// 쉬운 화면과 고급 터미널을 구분한 아홉 PAPER 화면을 제공한다.
import type { PageId } from '../types'

const items: { id: PageId; label: string }[] = [
  { id: 'live', label: '홈' },
  { id: 'strategies', label: '전략 리그' },
  { id: 'positions', label: '진행 거래' },
  { id: 'history', label: '거래 기록' },
  { id: 'replay', label: '과거 재생' },
  { id: 'performance', label: '성과' },
  { id: 'risk', label: '안전 설정' },
  { id: 'terminal', label: '고급 터미널' },
  { id: 'system', label: '시스템' },
]

type Props = { page: PageId; onChange: (page: PageId) => void }

export function Navigation({ page, onChange }: Props) {
  return (
    <nav className="navigation" aria-label="주요 화면">
      {items.map((item) => (
        <button
          type="button"
          key={item.id}
          className={page === item.id ? 'nav-button active' : 'nav-button'}
          aria-current={page === item.id ? 'page' : undefined}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </nav>
  )
}
