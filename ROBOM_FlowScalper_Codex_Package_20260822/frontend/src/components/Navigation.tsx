// V6의 네 개 사용자 화면만 제공하는 단일 주 메뉴다.
import type { PageId } from '../types'

const items: Array<{ id: PageId; label: string }> = [
  { id: 'market', label: '시장' },
  { id: 'strategies', label: '전략' },
  { id: 'trades', label: '거래' },
  { id: 'settings', label: '설정' },
]

type Props = { page: PageId; onChange: (page: PageId) => void }

export function Navigation({ page, onChange }: Props) {
  return (
    <nav className="navigation navigation-shell" aria-label="주요 화면">
      {items.map((item) => <button type="button" key={item.id} className={page === item.id ? 'nav-button active' : 'nav-button'} aria-current={page === item.id ? 'page' : undefined} onClick={() => onChange(item.id)}>{item.label}</button>)}
    </nav>
  )
}
