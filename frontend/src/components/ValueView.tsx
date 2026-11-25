import type { Field } from '../api'

export function formatValue(field: Field | undefined, value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  if (!field) return Array.isArray(value) ? value.join(', ') : String(value)
  const labels = new Map((field.config.choices ?? []).map((c) => [c.value, c.label || c.value]))
  if (field.type === 'options') {
    const list = Array.isArray(value) ? value : [value]
    return list.map((v) => labels.get(String(v)) ?? String(v)).join(', ')
  }
  if (field.type === 'list') return (value as unknown[]).map(String).join(', ')
  if (field.type === 'number') {
    const n = Number(value)
    const text = Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 4 })
    return field.config.unit ? `${text} ${field.config.unit}` : text
  }
  if (field.type === 'date') {
    const [y, m, d] = String(value).split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { dateStyle: 'medium' })
  }
  return String(value)
}

export default function ValueView({ field, value }: { field?: Field; value: unknown }) {
  const text = formatValue(field, value)
  if (!text) return <span className="muted">—</span>
  if (field?.type === 'options' || field?.type === 'list') {
    const list = Array.isArray(value) ? value : [value]
    const labels = new Map((field.config.choices ?? []).map((c) => [c.value, c.label || c.value]))
    return (
      <span className="chips">
        {list.map((v, i) => (
          <span className="chip" key={i}>
            {labels.get(String(v)) ?? String(v)}
          </span>
        ))}
      </span>
    )
  }
  return <span className={field?.type === 'text' && text.length > 80 ? 'prewrap' : ''}>{text}</span>
}
