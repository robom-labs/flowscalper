// 목록 크기를 바꾸지 않고 상세 정보를 보여주는 접근 가능한 고정 drawer다.
import { useEffect, useRef, type ReactNode } from 'react'

type Props = {
  title: string
  open: boolean
  onClose: () => void
  children: ReactNode
  label?: string
}

export function SideDrawer({ title, open, onClose, children, label = '상세 정보' }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previous?.focus()
    }
  }, [onClose, open])

  if (!open) return null
  return (
    <div className="drawer-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <aside className="side-drawer" role="dialog" aria-modal="true" aria-label={label}>
        <div className="drawer-heading">
          <h2>{title}</h2>
          <button ref={closeRef} type="button" className="close-button" onClick={onClose} aria-label={`${label} 닫기`}>닫기</button>
        </div>
        <div className="drawer-content">{children}</div>
      </aside>
    </div>
  )
}
