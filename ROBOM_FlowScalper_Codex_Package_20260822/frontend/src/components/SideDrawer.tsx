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
  const layerRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const backgroundState: Array<{ element: Element; inert: string | null; ariaHidden: string | null }> = []
    let pathElement: Element | null = layerRef.current
    while (pathElement?.parentElement && pathElement !== document.body) {
      const parent = pathElement.parentElement
      for (const sibling of parent.children) {
        if (sibling === pathElement) continue
        backgroundState.push({
          element: sibling,
          inert: sibling.getAttribute('inert'),
          ariaHidden: sibling.getAttribute('aria-hidden'),
        })
        sibling.setAttribute('inert', '')
        sibling.setAttribute('aria-hidden', 'true')
      }
      pathElement = parent
    }
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab' || !layerRef.current) return
      const focusable = [...layerRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )].filter((element) => !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true')
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable.at(-1) ?? first
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      for (const state of backgroundState) {
        if (state.inert === null) state.element.removeAttribute('inert')
        else state.element.setAttribute('inert', state.inert)
        if (state.ariaHidden === null) state.element.removeAttribute('aria-hidden')
        else state.element.setAttribute('aria-hidden', state.ariaHidden)
      }
      previous?.focus()
    }
  }, [onClose, open])

  if (!open) return null
  return (
    <div ref={layerRef} className="drawer-layer" role="presentation" onMouseDown={(event) => {
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
