import { useState } from 'react'
import type { Field, FieldConfig } from '../api'

interface Props {
  field: Field
  value: unknown
  onChange: (v: unknown) => void
  error?: string
}

function hint(field: Field): string {
  const c: FieldConfig = field.config
  const parts: string[] = []
  if (field.type === 'text') {
    if (c.min_length != null && c.max_length != null) parts.push(`${c.min_length}–${c.max_length} characters`)
    else if (c.max_length != null) parts.push(`Up to ${c.max_length} characters`)
    else if (c.min_length != null) parts.push(`At least ${c.min_length} characters`)
    if (c.regex) parts.push(c.regex_message ? `Must ${c.regex_message.replace(/^must /, '')}` : `Pattern: ${c.regex}`)
  }
  if (field.type === 'number' || field.type === 'date') {
    if (c.min != null && c.max != null) parts.push(`Between ${c.min} and ${c.max}`)
    else if (c.min != null) parts.push(`At least ${c.min}`)
    else if (c.max != null) parts.push(`At most ${c.max}`)
    if (c.integer_only) parts.push('Whole number')
  }
  if (field.type === 'options' && c.multiple) {
    if (c.max_selected) parts.push(`Pick up to ${c.max_selected}`)
    if (c.min_selected) parts.push(`Pick at least ${c.min_selected}`)
  }
  if (field.type === 'list' && c.max_items) parts.push(`Up to ${c.max_items} items`)
  return parts.join(' · ')
}

function ScalarInput({ type, value, onChange, config, placeholder }: {
  type: 'text' | 'number' | 'date'
  value: unknown
  onChange: (v: unknown) => void
  config: FieldConfig
  placeholder?: string
}) {
  if (type === 'number') {
    return (
      <input
        type="number"
        value={value === null || value === undefined ? '' : String(value)}
        step={config.integer_only ? 1 : 'any'}
        min={config.min as number | undefined}
        max={config.max as number | undefined}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      />
    )
  }
  if (type === 'date') {
    return (
      <input
        type="date"
        value={(value as string) ?? ''}
        min={config.min as string | undefined}
        max={config.max as string | undefined}
        onChange={(e) => onChange(e.target.value || null)}
      />
    )
  }
  const long = (config.max_length ?? 0) > 300
  return long ? (
    <textarea rows={4} value={(value as string) ?? ''} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
  ) : (
    <input type="text" value={(value as string) ?? ''} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
  )
}

function ListInput({ field, value, onChange }: Props) {
  const items = Array.isArray(value) ? (value as unknown[]) : []
  const [draft, setDraft] = useState<unknown>(null)
  const itemType = field.config.item_type ?? 'text'
  const add = () => {
    if (draft === null || draft === '') return
    onChange([...items, draft])
    setDraft(null)
  }
  return (
    <div className="list-input">
      <div className="chips">
        {items.map((it, i) => (
          <span className="chip" key={i}>
            {String(it)}
            <button type="button" className="chip-x" onClick={() => onChange(items.filter((_, j) => j !== i))} aria-label="Remove">
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row" onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), add())}>
        <ScalarInput type={itemType} value={draft} onChange={setDraft} config={field.config.item ?? {}} placeholder="Add an item" />
        <button type="button" onClick={add}>Add</button>
      </div>
    </div>
  )
}

export default function FieldInput(props: Props) {
  const { field, value, onChange, error } = props
  const c = field.config
  let input
  if (field.type === 'options' && c.multiple) {
    const selected = new Set(Array.isArray(value) ? (value as string[]) : [])
    input = (
      <div className="checks">
        {(c.choices ?? []).map((ch) => (
          <label key={ch.value} className="check">
            <input
              type="checkbox"
              checked={selected.has(ch.value)}
              onChange={(e) => {
                const next = new Set(selected)
                if (e.target.checked) next.add(ch.value)
                else next.delete(ch.value)
                onChange((c.choices ?? []).map((x) => x.value).filter((v) => next.has(v)))
              }}
            />
            {ch.label || ch.value}
          </label>
        ))}
      </div>
    )
  } else if (field.type === 'options') {
    input = (
      <select value={(value as string) ?? ''} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">—</option>
        {(c.choices ?? []).map((ch) => (
          <option key={ch.value} value={ch.value}>
            {ch.label || ch.value}
          </option>
        ))}
      </select>
    )
  } else if (field.type === 'list') {
    input = <ListInput {...props} />
  } else {
    input = <ScalarInput type={field.type} value={value} onChange={onChange} config={c} placeholder={c.placeholder} />
  }
  const h = hint(field)
  return (
    <div className={`form-field ${error ? 'has-error' : ''}`}>
      <label>
        {field.label}
        {field.required && <span className="req"> *</span>}
      </label>
      {field.description && <div className="muted small">{field.description}</div>}
      {input}
      {h && <div className="hint">{h}</div>}
      {error && <div className="field-error">{error}</div>}
    </div>
  )
}
