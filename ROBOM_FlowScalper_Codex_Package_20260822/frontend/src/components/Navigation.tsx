// 여섯 사용자 화면을 키보드로 접근 가능한 탭 형태로 제공한다.
import type { PageId } from '../types'

const items: { id: PageId; label: string }[] = [
  { id: 'live', label: '라이브' },
  { id: 'history', label: '거래내역' },
  { id: 'replay', label: '리플레이' },
  { id: 'performance', label: '성과분석' },
  { id: 'risk', label: '위험관리' },
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

