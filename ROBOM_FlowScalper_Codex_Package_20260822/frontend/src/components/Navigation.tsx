// 다섯 개 주 메뉴와 필요한 경우에만 보이는 하위 메뉴를 제공한다.
import type { PageId } from '../types'

const items: { id: PageId; label: string; pages: PageId[] }[] = [
  { id: 'terminal', label: '시장', pages: ['terminal'] },
  { id: 'strategies', label: '전략', pages: ['strategies', 'positions'] },
  { id: 'history', label: '기록', pages: ['history', 'replay'] },
  { id: 'performance', label: '분석', pages: ['performance', 'strategy-symbol'] },
  { id: 'system', label: '설정', pages: ['risk', 'system'] },
]

const secondary: Partial<Record<PageId, { id: PageId; label: string }[]>> = {
  strategies: [{ id: 'strategies', label: '전략 설정' }, { id: 'positions', label: '진행 중' }],
  positions: [{ id: 'strategies', label: '전략 설정' }, { id: 'positions', label: '진행 중' }],
  history: [{ id: 'history', label: '거래 기록' }, { id: 'replay', label: '과거 재생' }],
  replay: [{ id: 'history', label: '거래 기록' }, { id: 'replay', label: '과거 재생' }],
  performance: [{ id: 'performance', label: '전체 성과' }, { id: 'strategy-symbol', label: '전략별 종목' }],
  'strategy-symbol': [{ id: 'performance', label: '전체 성과' }, { id: 'strategy-symbol', label: '전략별 종목' }],
  risk: [{ id: 'risk', label: '안전' }, { id: 'system', label: '시스템' }],
  system: [{ id: 'risk', label: '안전' }, { id: 'system', label: '시스템' }],
}

type Props = { page: PageId; onChange: (page: PageId) => void }

export function Navigation({ page, onChange }: Props) {
  const hasSecondary = Boolean(secondary[page])
  return (
    <div className={hasSecondary ? 'navigation-shell has-secondary' : 'navigation-shell'}>
      <nav className="navigation" aria-label="주요 화면">
        {items.map((item) => {
          const active = item.pages.includes(page)
          return <button type="button" key={item.id} className={active ? 'nav-button active' : 'nav-button'} aria-current={active ? 'page' : undefined} onClick={() => onChange(item.id)}>{item.label}</button>
        })}
      </nav>
      {hasSecondary ? <nav className="secondary-navigation" aria-label="하위 화면">{secondary[page]?.map((item) => <button type="button" key={item.id} className={page === item.id ? 'active' : ''} onClick={() => onChange(item.id)}>{item.label}</button>)}</nav> : null}
    </div>
  )
}
