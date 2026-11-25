import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useRef, useState, type KeyboardEvent } from 'react'
import { api, atomName, qs, refName, type Atom, type Field, type Page } from '../api'

interface Props {
  field: Field
  value: unknown
  onChange: (v: unknown) => void
  error?: string
  /** The atom being edited, which a formula can't refer to. */
  atomId?: string
  /** The atoms the formula currently uses, as the server resolved them. */
  inputs?: { ref: string; atom_id: string; atom_name: string | null }[]
}

/** The @name being typed just before the caret, if any. */
function tokenAt(text: string, caret: number): { start: number; query: string } | null {
  const m = /@([a-z0-9_]*)$/i.exec(text.slice(0, caret))
  if (!m) return null
  const before = text.slice(0, m.index)
  // Not inside a string literal.
  if ((before.split('"').length - 1) % 2 === 1 || (before.split("'").length - 1) % 2 === 1) return null
  return { start: m.index, query: m[1] }
}

/** A formula box: typing @ suggests atoms and inserts their @name. */
export default function FormulaInput({ field, value, onChange, error, atomId, inputs }: Props) {
  const text = typeof value === 'string' ? value : ''
  const ref = useRef<HTMLTextAreaElement>(null)
  const [token, setToken] = useState<{ start: number; query: string } | null>(null)
  const [active, setActive] = useState(0)

  const search = useQuery({
    queryKey: ['formula-atoms', token?.query ?? ''],
    queryFn: () => api<Page<Atom>>(`/api/v1/atoms${qs({ q: token?.query.replace(/_/g, ' ').trim(), page_size: 50, sort: 'name' })}`),
    enabled: token !== null,
    placeholderData: keepPreviousData,
  })
  // Search matches any field, so keep only atoms whose @name contains what was typed, best matches first.
  const typed = token?.query.toLowerCase() ?? ''
  const options = (search.data?.items ?? [])
    .filter((a) => a.id !== atomId && a.status === 'active' && refName(a.values.name).includes(typed))
    .sort((a, b) => Number(!refName(a.values.name).startsWith(typed)) - Number(!refName(b.values.name).startsWith(typed)))
    .slice(0, 8)

  const update = (el: HTMLTextAreaElement) => {
    setToken(tokenAt(el.value, el.selectionStart))
    setActive(0)
  }

  const insert = (a: Atom) => {
    if (!token) return
    const name = `@${refName(a.values.name)}`
    const caret = token.start + 1 + token.query.length
    const next = text.slice(0, token.start) + name + text.slice(caret)
    onChange(next)
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
    if (!token || options.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => (i + 1) % options.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i - 1 + options.length) % options.length)
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      insert(options[Math.min(active, options.length - 1)])
    } else if (e.key === 'Escape') {
      setToken(null)
    }
  }

  return (
    <div className={`form-field ${error ? 'has-error' : ''}`}>
      <label>{field.label}</label>
      {field.description && <div className="muted small">{field.description}</div>}
      <div className="picker formula">
        <textarea
          ref={ref}
          rows={3}
          className="mono"
          value={text}
          placeholder="Type @ to pick an atom, e.g. @weight / (@height * @height)"
          onChange={(e) => {
            onChange(e.target.value || null)
            update(e.target)
          }}
          onKeyDown={onKeyDown}
          onClick={(e) => update(e.currentTarget)}
          onKeyUp={(e) => ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) && update(e.currentTarget)}
          onBlur={() => setTimeout(() => setToken(null), 150)}
        />
        {token && (
          <div className="picker-results">
            {search.isFetching && options.length === 0 && <div className="muted small">Searching…</div>}
            {!search.isFetching && options.length === 0 && <div className="muted small">No atoms match.</div>}
            {options.map((a, i) => (
              <button
                type="button"
                key={a.id}
                className={`picker-item ${i === active ? 'active' : ''}`}
                onMouseDown={(e) => (e.preventDefault(), insert(a))}
              >
                <code>@{refName(a.values.name)}</code> <span className="muted small">{atomName(a.values)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="hint">
        Leave empty for a normal question. With a formula, this atom is calculated on forms from the atoms you name with @. You can also use{' '}
        <code>today()</code>, <code>getFullYear()</code>, <code>double()</code> and the rest of CEL.
      </div>
      {inputs && inputs.length > 0 && (
        <div className="small">
          Uses:{' '}
          {inputs.map((inp, i) => (
            <span key={inp.ref}>
              {i > 0 && ', '}
              <code>@{inp.ref}</code> = {inp.atom_name ?? inp.atom_id}
            </span>
          ))}
        </div>
      )}
      {error && <div className="field-error">{error}</div>}
    </div>
  )
}
