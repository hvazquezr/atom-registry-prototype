import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, qs, type FormDetail, type FormSummary, type InputSettings, type Page } from '../api'
import { useAuth } from '../auth'
import ErrorBox, { fieldErrorMap } from '../components/ErrorBox'
import { useSchema } from '../hooks'

const ANSWER_TYPES = ['text', 'integer', 'number', 'date', 'boolean', 'choice', 'multiple_choice']

function slug(s: string) {
  const k = s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return /^[a-z]/.test(k) ? k.slice(0, 64) : k ? `f_${k}`.slice(0, 64) : ''
}

function NewForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [key, setKey] = useState('')
  const [keyTouched, setKeyTouched] = useState(false)
  const [description, setDescription] = useState('')
  const create = useMutation({
    mutationFn: () => api<FormDetail>('/api/v1/forms', { body: { key: keyTouched ? key : slug(name), name, description } }),
    onSuccess: (f) => navigate(`/forms/${f.id}`),
  })
  const errs = fieldErrorMap(create.error)
  return (
    <div className="card inset">
      <h3>New form</h3>
      <div className="grid2">
        <div className={`form-field ${errs.name ? 'has-error' : ''}`}>
          <label>Name <span className="req">*</span></label>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="New customer" />
        </div>
        <div className={`form-field ${errs.key ? 'has-error' : ''}`}>
          <label>Key <span className="req">*</span></label>
          <input className="mono" value={keyTouched ? key : slug(name)} onChange={(e) => (setKeyTouched(true), setKey(e.target.value))} />
          <div className="hint">Used in API URLs. Can't be changed later.</div>
        </div>
      </div>
      <div className="form-field">
        <label>Description</label>
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <ErrorBox error={create.error} />
      <div className="actions">
        <button className="primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>Create and start building</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function InputSettingsCard() {
  const qc = useQueryClient()
  const schema = useSchema()
  const settings = useQuery({ queryKey: ['form-input-settings'], queryFn: () => api<InputSettings>('/api/v1/form-input-settings') })
  const [draft, setDraft] = useState<InputSettings | null>(null)
  const save = useMutation({
    mutationFn: (s: InputSettings) => api<InputSettings>('/api/v1/form-input-settings', { method: 'PUT', body: s }),
    onSuccess: (s) => {
      qc.setQueryData(['form-input-settings'], s)
      qc.invalidateQueries({ queryKey: ['form'] })
      setDraft(null)
    },
  })
  const s = draft ?? settings.data
  if (!s) return null
  const fields = schema.data?.fields ?? []
  const typeField = fields.find((f) => f.key === s.type_field)
  const typeValues = typeField?.type === 'options' ? (typeField.config.choices ?? []).map((c) => c.value) : Object.keys(s.type_map)
  const set = (patch: Partial<InputSettings>) => setDraft({ ...s, ...patch })
  const pick = (label: string, k: keyof InputSettings, types: string[]) => (
    <div className="form-field">
      <label>{label}</label>
      <select value={(s[k] as string | null) ?? ''} onChange={(e) => set({ [k]: e.target.value || null } as Partial<InputSettings>)}>
        <option value="">None</option>
        {fields.filter((f) => types.includes(f.type)).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
      </select>
    </div>
  )
  return (
    <details className="card">
      <summary className="strong">How atoms become questions</summary>
      <p className="muted small">
        Each atom on a form gets an input whose type and rules come from the atom's own fields. Published versions keep the inputs they were
        published with.
      </p>
      <div className="grid2">
        {pick('Answer type comes from', 'type_field', ['options', 'text'])}
        <div className="form-field">
          <label>Otherwise use</label>
          <select value={s.default_type} onChange={(e) => set({ default_type: e.target.value })}>
            {ANSWER_TYPES.filter((t) => !t.includes('choice')).map((t) => <option key={t}>{t}</option>)}
          </select>
        </div>
      </div>
      {s.type_field && (
        <table className="table compact">
          <thead><tr><th>When {typeField?.label ?? s.type_field} is</th><th>The answer is</th></tr></thead>
          <tbody>
            {typeValues.map((v) => (
              <tr key={v}>
                <td className="mono">{v}</td>
                <td>
                  <select value={s.type_map[v] ?? ''} onChange={(e) => {
                    const m = { ...s.type_map }
                    if (e.target.value) m[v] = e.target.value
                    else delete m[v]
                    set({ type_map: m })
                  }}>
                    <option value="">(default)</option>
                    {ANSWER_TYPES.map((t) => <option key={t}>{t}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="grid2">
        {pick('Minimum length', 'min_length_field', ['number'])}
        {pick('Maximum length', 'max_length_field', ['number'])}
        {pick('Minimum value', 'min_field', ['number', 'date'])}
        {pick('Maximum value', 'max_field', ['number', 'date'])}
        {pick('Pattern (regex)', 'regex_field', ['text'])}
        {pick('Allowed answers', 'choices_field', ['list'])}
        {pick('Answer formula (@ refers to other atoms)', 'formula_field', ['text'])}
      </div>
      <ErrorBox error={save.error} />
      {draft && (
        <div className="actions">
          <button className="primary" disabled={save.isPending} onClick={() => save.mutate(draft)}>Save</button>
          <button onClick={() => setDraft(null)}>Discard</button>
        </div>
      )}
    </details>
  )
}

export default function FormList() {
  const { can } = useAuth()
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('active')
  const [creating, setCreating] = useState(false)
  const forms = useQuery({
    queryKey: ['forms', search, status],
    queryFn: () => api<Page<FormSummary>>(`/api/v1/forms${qs({ q: search, status, page_size: 100 })}`),
    placeholderData: keepPreviousData,
  })
  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Forms</h1>
          <div className="muted small">Atoms grouped into sections. Drafts can be edited freely; publishing a version is reviewed.</div>
        </div>
        {can('forms:write') && !creating && <button className="primary" onClick={() => setCreating(true)}>New form</button>}
      </div>
      {creating && <NewForm onCancel={() => setCreating(false)} />}
      <form className="toolbar" onSubmit={(e) => (e.preventDefault(), setSearch(q))}>
        <input type="search" className="grow" placeholder="Search forms…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button>Search</button>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="active">Active</option>
          <option value="deprecated">Deprecated</option>
          <option value="all">All</option>
        </select>
      </form>
      <ErrorBox error={forms.error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Form</th><th>Atoms</th><th>Published</th><th>Updated</th></tr></thead>
          <tbody>
            {forms.data?.items.map((f) => (
              <tr key={f.id}>
                <td>
                  <Link className="strong" to={`/forms/${f.id}`}>{f.name}</Link>
                  {f.status === 'deprecated' && <span className="pill gray">deprecated</span>}
                  <div className="muted small mono">{f.key}</div>
                </td>
                <td>{f.item_count}</td>
                <td>{f.latest_version ? <span className="pill approved">v{f.latest_version}</span> : <span className="pill gray">draft only</span>}</td>
                <td className="muted small nowrap">{new Date(f.updated_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {forms.data?.items.length === 0 && <tr><td colSpan={4} className="muted center">No forms yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {can('schema:admin') && <InputSettingsCard />}
    </div>
  )
}
