import { useEffect, useRef, useState, type ReactNode } from 'react'

interface TriggerArgs {
  open: boolean
  toggle: () => void
  close: () => void
}

interface Props {
  // The clickable element that opens/closes the popover; receives the open state.
  trigger: (args: TriggerArgs) => ReactNode
  // Popover contents. The function form receives `close` so items can dismiss it.
  children: ReactNode | ((args: { close: () => void }) => ReactNode)
  // Extra class on the anchor wrapper (e.g. to position it in a row).
  className?: string
  // Extra class on the popover itself (visual/content styling).
  menuClassName?: string
  menuRole?: string
  menuLabel?: string
}

/**
 * Small reusable popover: an anchored dropdown that opens *above* its trigger
 * (the composer lives at the bottom of the window) and closes on an outside
 * click or Escape. Shared by the composer's attach menu and the thinking chip.
 */
export default function Dropdown({
  trigger,
  children,
  className,
  menuClassName,
  menuRole = 'menu',
  menuLabel,
}: Props): JSX.Element {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const close = (): void => setOpen(false)
  const toggle = (): void => setOpen((o) => !o)

  // Dismiss on an outside click or the Escape key while open.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent): void => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className={`dropdown${className ? ` ${className}` : ''}`} ref={wrapRef}>
      {trigger({ open, toggle, close })}
      {open && (
        <div
          className={`dropdown-menu${menuClassName ? ` ${menuClassName}` : ''}`}
          role={menuRole}
          aria-label={menuLabel}
        >
          {typeof children === 'function' ? children({ close }) : children}
        </div>
      )}
    </div>
  )
}
