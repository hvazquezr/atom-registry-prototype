import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, atomName, type Atom, type ChangeRequest, type FieldError, type Values } from '../api'
import ErrorBox, { fieldErrorMap } from '../components/ErrorBox'
import FieldInput from '../components/FieldInput'
import ValueView from '../components/ValueView'
import { editableFields, useSchema } from '../hooks'

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

const isEmpty = (v: unknown) => v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)

export default function AtomEdit() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const schema = useSchema()
  const atom = useQuery({ queryKey: ['atom', id], queryFn: () => api<Atom>(`/api/v1/atoms/${id}`), enabled: !!id })
  const [values, setValues] = useState<Values>({})
  const [comment, setComment] = useState('')
  const [touched, setTouched] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (atom.data) setValues(atom.data.values)
  }, [atom.data])

  const fields = schema.data?.fields ?? []
  const inputs = editableFields(fields)
  const calculated = fields.filter((f) => f.calculated)

  // What to send: everything for a new atom, only changed keys for an edit (null clears a value).
  const payload = useMemo(() => {
    const out: Values = {}
    for (const f of inputs) {
      const v = values[f.key]
      if (!id) {
        if (!isEmpty(v)) out[f.key] = v
      } else if (JSON.stringify(v ?? null) !== JSON.stringify(atom.data?.values[f.key] ?? null)) {
        out[f.key] = isEmpty(v) ? null : v
      }
    }
    return out
  }, [values, inputs, id, atom.data])

  const debounced = useDebounced(payload, 350)
  const preview = useQuery({
    queryKey: ['preview', id, debounced],
    queryFn: () => api<{ valid: boolean; values: Values | null; errors: FieldError[] }>('/api/v1/atoms/preview', { body: { values: debounced, atom_id: id } }),
    enabled: !!schema.data && (!id || !!atom.data),
  })
  const previewErrors: Record<string, string> = {}
  for (const e of preview.data?.errors ?? []) if (touched.has(e.field) || e.code !== 'required') previewErrors[e.field] = e.message

  const submit = useMutation({
    mutationFn: () =>
      id
        ? api<ChangeRequest>(`/api/v1/atoms/${id}`, { method: 'PATCH', body: { values: payload, base_revision: atom.data?.revision, comment } })
        : api<ChangeRequest>('/api/v1/atoms', { body: { values: payload, comment } }),
    onSuccess: (cr) => {
      qc.invalidateQueries({ queryKey: ['review-queue'] })
      qc.invalidateQueries({ queryKey: ['atom', id] })
      navigate(`/requests/${cr.id}`)
    },
  })
  const submitErrors = fieldErrorMap(submit.error)

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(new Set(inputs.map((f) => f.key)))
    submit.mutate()
  }

  if (schema.error) return <ErrorBox error={schema.error} />
  if (!schema.data || (id && !atom.data)) return <div className="muted">Loading…</div>
  const unchanged = !!id && Object.keys(payload).length === 0

  return (
    <form onSubmit={onSubmit}>
      <div className="page-head">
        <div>
          <div className="muted small">
            <Link to="/atoms">Atoms</Link> / {id && <Link to={`/atoms/${id}`}>{atomName(atom.data?.values)}</Link>}
          </div>
          <h1>{id ? `Edit ${atomName(atom.data?.values)}` : 'New atom'}</h1>
          <div className="muted small">Changes are reviewed before they go live.</div>
        </div>
      </div>
      <div className="two-col">
        <div className="card">
          {inputs.map((f) => (
            <FieldInput
              key={f.key}
              field={f}
              value={values[f.key]}
              error={submitErrors[f.key] ?? previewErrors[f.key]}
              onChange={(v) => {
                setValues({ ...values, [f.key]: v })
                setTouched(new Set(touched).add(f.key))
              }}
            />
          ))}
          <div className="form-field">
            <label>Comment for the reviewer</label>
            <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="What changed and why" />
          </div>
          <ErrorBox error={submit.error} />
          <div className="actions">
            <button className="primary" disabled={submit.isPending || unchanged}>
              {submit.isPending ? 'Submitting…' : 'Submit for review'}
            </button>
            <button type="button" onClick={() => navigate(-1)}>Cancel</button>
            {unchanged && <span className="muted small">No changes yet.</span>}
          </div>
        </div>
        {calculated.length > 0 && (
          <div className="card side">
            <h3>Calculated</h3>
            <div className="muted small">Updated as you type. The server calculates the stored values.</div>
            <dl className="values compact">
              {calculated.map((f) => (
                <div key={f.key} className="value-row">
                  <dt title={f.expression ?? ''}>{f.label}</dt>
                  <dd>
                    {previewErrors[f.key] ? (
                      <span className="field-error">{previewErrors[f.key]}</span>
                    ) : (
                      <ValueView field={f} value={preview.data?.values?.[f.key]} />
                    )}
                  </dd>
                  <code className="expr">{f.expression}</code>
                </div>
              ))}
            </dl>
          </div>
        )}
      </div>
    </form>
  )
}
