import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Choice, type Field, type FieldConfig, type FieldType } from '../api'
import ErrorBox, { fieldErrorMap } from './ErrorBox'
import FieldInput from './FieldInput'
import RefTextarea from './RefTextarea'
import ValueView from './ValueView'

const TYPES: { value: FieldType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'options', label: 'Options (single or multiple choice)' },
  { value: 'list', label: 'List' },
]

const blank: Field = {
  key: '', label: '', description: '', type: 'text', required: false, config: {}, calculated: false,
  expression: '', depends_on: [], time_dependent: false, position: 0,
}

function num(v: string): number | undefined {
  return v === '' ? undefined : Number(v)
}

function clean(config: FieldConfig): FieldConfig {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(config)) if (v !== undefined && v !== '' && v !== false && !(typeof v === 'number' && Number.isNaN(v))) out[k] = v
  return out as FieldConfig
}

function ChoicesEditor({ choices, onChange }: { choices: Choice[]; onChange: (c: Choice[]) => void }) {
  return (
    <div className="choices">
      {choices.map((c, i) => (
        <div className="row" key={i}>
          <input placeholder="value" value={c.value} onChange={(e) => onChange(choices.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
          <input placeholder="label (optional)" value={c.label ?? ''} onChange={(e) => onChange(choices.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
          <button type="button" className="link danger" onClick={() => onChange(choices.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...choices, { value: '', label: '' }])}>Add choice</button>
    </div>
  )
}

function ConfigEditor({ type, config, onChange }: { type: FieldType; config: FieldConfig; onChange: (c: FieldConfig) => void }) {
  const set = (patch: FieldConfig) => onChange({ ...config, ...patch })
  const [sample, setSample] = useState('')
  let regexOk: boolean | null = null
  if (type === 'text' && config.regex && sample) {
    try {
      regexOk = new RegExp(`^(?:${config.regex})$`).test(sample)
    } catch {
      regexOk = null
    }
  }
  switch (type) {
    case 'text':
      return (
        <>
          <div className="grid2">
            <div className="form-field"><label>Minimum length</label><input type="number" min={0} value={config.min_length ?? ''} onChange={(e) => set({ min_length: num(e.target.value) })} /></div>
            <div className="form-field"><label>Maximum length</label><input type="number" min={0} value={config.max_length ?? ''} onChange={(e) => set({ max_length: num(e.target.value) })} /></div>
          </div>
          <div className="form-field">
            <label>Regex the whole value must match</label>
            <input className="mono" value={config.regex ?? ''} placeholder="[A-Z]{3}-[0-9]{3}" onChange={(e) => set({ regex: e.target.value })} />
            <div className="hint">Checked with RE2 on the server (no backreferences or lookarounds).</div>
          </div>
          {config.regex && (
            <>
              <div className="form-field"><label>Message when it doesn't match</label><input value={config.regex_message ?? ''} placeholder="must look like ABC-123" onChange={(e) => set({ regex_message: e.target.value })} /></div>
              <div className="form-field">
                <label>Try a value</label>
                <input value={sample} onChange={(e) => setSample(e.target.value)} />
                {regexOk !== null && <div className={regexOk ? 'ok' : 'field-error'}>{regexOk ? 'Matches' : "Doesn't match"}</div>}
              </div>
            </>
          )}
        </>
      )
    case 'number':
      return (
        <>
          <div className="grid2">
            <div className="form-field"><label>Minimum</label><input type="number" step="any" value={config.min ?? ''} onChange={(e) => set({ min: num(e.target.value) })} /></div>
            <div className="form-field"><label>Maximum</label><input type="number" step="any" value={config.max ?? ''} onChange={(e) => set({ max: num(e.target.value) })} /></div>
          </div>
          <div className="grid2">
            <label className="check"><input type="checkbox" checked={!!config.integer_only} onChange={(e) => set({ integer_only: e.target.checked })} /> Whole numbers only</label>
            <div className="form-field"><label>Unit</label><input value={config.unit ?? ''} placeholder="USD, kg…" onChange={(e) => set({ unit: e.target.value })} /></div>
          </div>
        </>
      )
    case 'date':
      return (
        <div className="grid2">
          <div className="form-field"><label>Earliest</label><input type="date" value={(config.min as string) ?? ''} onChange={(e) => set({ min: e.target.value || undefined })} /></div>
          <div className="form-field"><label>Latest</label><input type="date" value={(config.max as string) ?? ''} onChange={(e) => set({ max: e.target.value || undefined })} /></div>
        </div>
      )
    case 'options':
      return (
        <>
          <div className="form-field">
            <label>Choices</label>
            <ChoicesEditor choices={config.choices ?? []} onChange={(choices) => set({ choices })} />
          </div>
          <div className="row">
            <label className="check"><input type="radio" checked={!config.multiple} onChange={() => set({ multiple: false, min_selected: undefined, max_selected: undefined })} /> Single choice</label>
            <label className="check"><input type="radio" checked={!!config.multiple} onChange={() => set({ multiple: true })} /> Multiple choice</label>
          </div>
          {config.multiple && (
            <div className="grid2">
              <div className="form-field"><label>At least</label><input type="number" min={0} value={config.min_selected ?? ''} onChange={(e) => set({ min_selected: num(e.target.value) })} /></div>
              <div className="form-field"><label>At most</label><input type="number" min={0} value={config.max_selected ?? ''} onChange={(e) => set({ max_selected: num(e.target.value) })} /></div>
            </div>
          )}
        </>
      )
    case 'list':
      return (
        <>
          <div className="form-field">
            <label>Item type</label>
            <select value={config.item_type ?? 'text'} onChange={(e) => set({ item_type: e.target.value as 'text', item: {} })}>
              <option value="text">Text</option>
              <option value="number">Number</option>
              <option value="date">Date</option>
            </select>
          </div>
          <div className="grid2">
            <div className="form-field"><label>Minimum items</label><input type="number" min={0} value={config.min_items ?? ''} onChange={(e) => set({ min_items: num(e.target.value) })} /></div>
            <div className="form-field"><label>Maximum items</label><input type="number" min={0} value={config.max_items ?? ''} onChange={(e) => set({ max_items: num(e.target.value) })} /></div>
          </div>
          <label className="check"><input type="checkbox" checked={!!config.unique_items} onChange={(e) => set({ unique_items: e.target.checked })} /> No duplicates</label>
          <details>
            <summary className="muted small">Rules for each item</summary>
            <ConfigEditor type={config.item_type ?? 'text'} config={config.item ?? {}} onChange={(item) => set({ item: clean(item) })} />
          </details>
        </>
      )
  }
}

interface Check {
  ok: boolean
  depends_on?: string[]
  time_dependent?: boolean
  sample_result?: unknown
  result?: unknown
  error?: { code: string; message: string }
}

function ExpressionEditor({ field, fields, onChange }: { field: Field; fields: Field[]; onChange: (expr: string) => void }) {
  const [debounced, setDebounced] = useState(field.expression ?? '')
  const [testValues, setTestValues] = useState<Record<string, unknown>>({})
  useEffect(() => {
    const t = setTimeout(() => setDebounced(field.expression ?? ''), 350)
    return () => clearTimeout(t)
  }, [field.expression])
  const check = useQuery({
    queryKey: ['check-expression', debounced, field.type, field.config, field.key, testValues],
    queryFn: () =>
      api<Check>('/internal/fields/check-expression', {
        body: { expression: debounced, type: field.type, config: clean(field.config), key: field.key || '_preview', values: Object.keys(testValues).length ? testValues : undefined },
      }),
    enabled: debounced.trim().length > 0,
  })
  const others = fields.filter((f) => f.key !== field.key && f.status !== 'deprecated').map((f) => ({ name: f.key, label: f.label }))
  const inputs = (check.data?.depends_on ?? []).map((k) => fields.find((f) => f.key === k)).filter(Boolean) as Field[]
  const missing = inputs.some((f) => testValues[f.key] === undefined || testValues[f.key] === null || testValues[f.key] === '')
  return (
    <div className="expr-editor">
      <div className="form-field">
        <label>Expression (CEL)</label>
        <RefTextarea value={field.expression ?? ''} onChange={onChange} options={others}
          placeholder="Type @ to pick a field, e.g. @unit_price * @quantity" />
        <div className="hint">
          Refer to other fields with @ and their key, like <code>@unit_price</code> (the same as atom formulas). Functions: today(), now(),
          date("2026-01-31"), days_between(a, b), size(), contains(), startsWith(), conditions with <code>cond ? a : b</code>.
        </div>
      </div>
      {check.data && !check.data.ok && <div className="field-error">{check.data.error?.message}</div>}
      {check.data?.ok && (
        <div className="ok small">
          Valid. Uses {check.data.depends_on?.length ? check.data.depends_on.map((k) => `@${k}`).join(', ') : 'no other fields'}
          {check.data.time_dependent && ' and the current date, so it is recalculated whenever the atom is read'}.
        </div>
      )}
      {check.data?.ok && inputs.length > 0 && (
        <div className="card inset">
          <h4>Try it</h4>
          {inputs.map((f) => (
            <FieldInput key={f.key} field={f} value={testValues[f.key]} onChange={(v) => setTestValues({ ...testValues, [f.key]: v })} />
          ))}
          {!missing && check.data.result !== undefined && (
            <div>Result: <strong><ValueView field={field} value={check.data.result} /></strong></div>
          )}
        </div>
      )}
    </div>
  )
}

function keyable(f: Field) {
  if (f.calculated) return false
  if (f.type === 'options') return !f.config.multiple
  return f.type === 'text' || f.type === 'number' || f.type === 'date'
}

export default function FieldEditor({ field, fields, onClose, readOnly }: { field: Field | null; fields: Field[]; onClose: () => void; readOnly: boolean }) {
  const [f, setF] = useState<Field>(field ?? { ...blank, position: fields.length })
  const isNew = !field?.id
  const save = useMutation({
    mutationFn: () => {
      const body = {
        key: f.key, label: f.label, description: f.description, type: f.type, required: f.required || !!f.natural_key,
        natural_key: !!f.natural_key, config: clean(f.config), calculated: f.calculated, expression: f.calculated ? f.expression : null,
      }
      return isNew ? api<Field>('/internal/fields', { body }) : api<Field>(`/internal/fields/${field!.id}`, { method: 'PATCH', body })
    },
    onSuccess: onClose,
  })
  const errors = fieldErrorMap(save.error)
  const autoKey = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(\d)/, 'f_$1').slice(0, 64)

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="row spread">
          <h2>{isNew ? 'New field' : f.label}</h2>
          <button className="link" onClick={onClose}>Close</button>
        </div>
        <fieldset disabled={readOnly}>
          <div className="grid2">
            <div className={`form-field ${errors.label ? 'has-error' : ''}`}>
              <label>Label</label>
              <input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value, key: isNew && (!f.key || f.key === autoKey(f.label)) ? autoKey(e.target.value) : f.key })} />
              {errors.label && <div className="field-error">{errors.label}</div>}
            </div>
            <div className={`form-field ${errors.key ? 'has-error' : ''}`}>
              <label>Key</label>
              <input className="mono" value={f.key} disabled={!!field?.published} onChange={(e) => setF({ ...f, key: e.target.value })} />
              <div className="hint">{field?.published ? 'Published keys are fixed.' : 'Used in the API and in expressions.'}</div>
              {errors.key && <div className="field-error">{errors.key}</div>}
            </div>
          </div>
          <div className="form-field">
            <label>Description</label>
            <textarea rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
          </div>
          <div className="grid2">
            <div className="form-field">
              <label>Type</label>
              <select value={f.type} disabled={field?.system} onChange={(e) => setF({ ...f, type: e.target.value as FieldType, config: e.target.value === 'options' ? { choices: [{ value: '', label: '' }] } : {} })}>
                {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div className="form-field">
              <label>&nbsp;</label>
              <div className="row">
                <label className="check" title={f.natural_key ? 'Natural key fields are always mandatory.' : undefined}>
                  <input type="checkbox" checked={f.required || !!f.natural_key} disabled={!!f.natural_key} onChange={(e) => setF({ ...f, required: e.target.checked })} /> Mandatory
                </label>
                <label className="check"><input type="checkbox" checked={f.calculated} disabled={field?.system || !!f.natural_key} onChange={(e) => setF({ ...f, calculated: e.target.checked })} /> Calculated</label>
              </div>
            </div>
          </div>
          <div className={`form-field ${errors.natural_key ? 'has-error' : ''}`}>
            <label className="check">
              <input type="checkbox" checked={!!f.natural_key} disabled={!keyable(f) && !f.natural_key}
                onChange={(e) => setF({ ...f, natural_key: e.target.checked, required: e.target.checked ? true : f.required })} /> Natural key
            </label>
            <div className="hint">
              {keyable(f) || f.natural_key
                ? 'Identifies an atom: no two atoms can share it, and the API can get an atom by it (GET /api/v1/atoms/key/{value}). Tick more than one field for a combined key.'
                : 'Only text, number, date and single-choice fields that aren\'t calculated can be part of the natural key.'}
            </div>
            {errors.natural_key && <div className="field-error">{errors.natural_key}</div>}
          </div>
          {f.calculated && <ExpressionEditor field={f} fields={fields} onChange={(expression) => setF({ ...f, expression })} />}
          <h4>Validation</h4>
          <ConfigEditor type={f.type} config={f.config} onChange={(config) => setF({ ...f, config })} />
          <ErrorBox error={save.error} />
          {!readOnly && (
            <div className="actions">
              <button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>{isNew ? 'Add field' : 'Save'}</button>
              <span className="muted small">Saved to the draft. Publish the schema to make it live.</span>
            </div>
          )}
        </fieldset>
      </div>
    </div>
  )
}
