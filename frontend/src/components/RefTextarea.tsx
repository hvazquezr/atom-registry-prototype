import { useRef, useState, type KeyboardEvent } from 'react'
import { tokenAt } from './FormulaInput'

export interface RefOption {
  name: string
  label: string
}

/** A textarea where typing @ suggests names from a fixed list and inserts @name. */
export default function RefTextarea({ value, onChange, options, placeholder, rows = 3 }: {
  value: string
  onChange: (v: string) => void
  options: RefOption[]
  placeholder?: string
  rows?: number
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [token, setToken] = useState<{ start: number; query: string } | null>(null)
  const [active, setActive] = useState(0)
  const typed = token?.query.toLowerCase() ?? ''
  const matches = options
    .filter((o) => o.name.includes(typed) || o.label.toLowerCase().includes(typed))
    .sort((a, b) => Number(!a.name.startsWith(typed)) - Number(!b.name.startsWith(typed)))
    .slice(0, 8)

  const update = (el: HTMLTextAreaElement) => {
    setToken(tokenAt(el.value, el.selectionStart))
    setActive(0)
  }

  const insert = (o: RefOption) => {
    if (!token) return
    const name = `@${o.name}`
    const caret = token.start + 1 + token.query.length
    onChange(value.slice(0, token.start) + name + value.slice(caret))
    setToken(null)
    requestAnimationFrame(() => {
      const el = ref.current
      if (!el) return
      el.focus()
      const pos = token.start + name.length
      el.setSelectionRange(pos, pos)
    })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!token || matches.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => (i + 1) % matches.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i - 1 + matches.length) % matches.length)
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      insert(matches[Math.min(active, matches.length - 1)])
    } else if (e.key === 'Escape') {
      setToken(null)
    }
  }

  return (
    <div className="picker formula">
      <textarea
        ref={ref}
        rows={rows}
        className="mono"
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value)
          update(e.target)
        }}
        onKeyDown={onKeyDown}
        onClick={(e) => update(e.currentTarget)}
        onKeyUp={(e) => ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) && update(e.currentTarget)}
        onBlur={() => setTimeout(() => setToken(null), 150)}
      />
      {token && (
        <div className="picker-results">
          {matches.length === 0 && <div className="muted small">No fields match.</div>}
          {matches.map((o, i) => (
            <button type="button" key={o.name} className={`picker-item ${i === active ? 'active' : ''}`}
              onMouseDown={(e) => (e.preventDefault(), insert(o))}>
              <code>@{o.name}</code> <span className="muted small">{o.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
