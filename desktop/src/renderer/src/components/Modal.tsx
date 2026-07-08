import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { CloseIcon } from './icons'

interface Props {
  open: boolean
  onClose: () => void
  title?: string
  icon?: ReactNode
  children: ReactNode
}

/**
 * A lightweight, animated modal. The frame appears instantly (so async content
 * loading inside never shows a blank window), closes on Escape or backdrop click.
 */
export function Modal({ open, onClose, title, icon, children }: Props): JSX.Element | null {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          {icon}
          {title && <h2 className="modal-title">{title}</h2>}
          <span className="spacer" />
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}
