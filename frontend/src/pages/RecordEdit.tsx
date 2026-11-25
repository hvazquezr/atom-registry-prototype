import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type AnswerCheck, type FormDefinition, type RecordItem, type RecordRevision, type Values } from '../api'
import { useAuth } from '../auth'
import ErrorBox, { fieldErrorMap } from '../components/ErrorBox'
import FormFields from '../components/FormFields'
import { formatDate } from '../hooks'

function calculatedKeys(d: FormDefinition | undefined): Set<string> {
  return new Set(d?.sections.flatMap((s) => s.items).filter((i) => i.calculation).map((i) => i.key) ?? [])
}

function entered(answers: Values, d: FormDefinition | undefined): Values {
  const calc = calculatedKeys(d)
  return Object.fromEntries(Object.entries(answers).filter(([k]) => !calc.has(k)))
}

/** Only the answers that differ from what is saved; cleared ones become null. */
function changes(saved: Values, now: Values): Values {
  const out: Values = {}
  for (const k of new Set([...Object.keys(saved), ...Object.keys(now)])) {
    if (JSON.stringify(saved[k]) !== JSON.stringify(now[k])) out[k] = k in now ? now[k] : null
  }
  return out
}

function History({ id }: { id: string }) {
  const [open, setOpen] = useState<number | null>(null)
  const revs = useQuery({ queryKey: ['record-revisions', id], queryFn: () => api<RecordRevision[]>(`/api/v1/records/${id}/revisions`) })
  return (
    <div className="card">
      <h3>History</h3>
      <ErrorBox error={revs.error} />
      <ul className="plain">
        {revs.data?.map((r) => (
          <li key={r.revision}>
            <button className="link" onClick={() => setOpen(open === r.revision ? null : r.revision)}>
              Revision {r.revision}
            </button>{' '}
            <span className="muted small">{r.summary}</span>
            <div className="muted small">{r.changed_by_name} · {formatDate(r.changed_at)} · v{r.form_version}{r.complete ? '' : ' · incomplete'}</div>
            {open === r.revision && <pre className="json">{JSON.stringify(r.answers, null, 2)}</pre>}
          </li>
        ))}
      </ul>
    </div>
  )
}

// Keyed by id so local edits reset when another record is opened.
export default function RecordEdit() {
  const { id } = useParams()
  return <RecordPage key={id ?? 'new'} id={id} />
}

function RecordPage({ id }: { id: string | undefined }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { can } = useAuth()
  const isNew = !id

  const record = useQuery({
    queryKey: ['record', id],
    queryFn: () => api<RecordItem>(`/api/v1/records/${id}`),
    enabled: !isNew,
  })
  const formKey = isNew ? params.get('form') ?? '' : record.data?.form.id
  const version = isNew ? 'latest' : record.data ? String(record.data.form_version) : undefined
  const definition = useQuery({
    queryKey: ['form-version', formKey, version],
    queryFn: () => api<FormDefinition>(`/api/v1/forms/${formKey}/versions/${version}`),
    enabled: !!formKey && !!version,
  })

  const saved = record.data && definition.data ? entered(record.data.answers, definition.data) : {}
  const [answers, setAnswers] = useState<Values | null>(null)
  const current = answers ?? saved
  const [notice, setNotice] = useState<string | null>(null)

  const [debounced, setDebounced] = useState<Values>(current)
  const currentJson = JSON.stringify(current)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(JSON.parse(currentJson)), 200)
    return () => clearTimeout(t)
  }, [currentJson])
  const check = useQuery({
    queryKey: ['record-check', formKey, version, debounced],
    queryFn: () => api<AnswerCheck>(`/api/v1/forms/${formKey}/validate`, {
      body: { version: version === 'latest' ? 'latest' : Number(version), answers: debounced },
    }),
    enabled: !!formKey && !!version && !!definition.data,
    placeholderData: keepPreviousData,
  })

  const onSaved = (r: RecordItem) => {
    qc.setQueryData(['record', r.id], r)
    qc.invalidateQueries({ queryKey: ['records'] })
    qc.invalidateQueries({ queryKey: ['record-revisions', r.id] })
    setAnswers(null)
  }
  const create = useMutation({
    mutationFn: () => api<RecordItem>('/api/v1/records', { body: { form: formKey, answers: current } }),
    onSuccess: (r) => (onSaved(r), navigate(`/records/${r.id}`, { replace: true })),
  })
  const save = useMutation({
    mutationFn: (body: { answers: Values; form_version?: 'latest' }) =>
      api<RecordItem>(`/api/v1/records/${id}`, { method: 'PATCH', body, headers: { 'If-Match': `"${record.data?.revision}"` } }),
    onSuccess: (r) => {
      onSaved(r)
      setNotice(r.dropped?.length ? `Moved to version ${r.form_version}. Answers to questions that version doesn't have were removed: ${r.dropped.join(', ')}.` : null)
    },
  })
  const setStatus = useMutation({
    mutationFn: (action: 'archive' | 'restore') =>
      api<RecordItem>(`/api/v1/records/${id}/${action}`, { body: {}, headers: { 'If-Match': `"${record.data?.revision}"` } }),
    onSuccess: onSaved,
  })

  const r = record.data
  const readOnly = !can('records:write') || r?.status === 'archived'
  const mutationError = create.error ?? save.error ?? setStatus.error
  const errors: Record<string, string> = {}
  for (const e of check.data?.errors ?? []) {
    if (e.code === 'required') continue
    errors[e.field] = errors[e.field] ? `${errors[e.field]} ${e.message}` : e.message
  }
  Object.assign(errors, fieldErrorMap(mutationError))
  const missing = (check.data?.errors ?? []).filter((e) => e.code === 'required')
  const labels = new Map(definition.data?.sections.flatMap((s) => s.items).map((i) => [i.key, i.label]))
  const diff = changes(saved, current)
  const dirty = isNew ? Object.keys(current).length > 0 : Object.keys(diff).length > 0
  const blocked = Object.keys(errors).length > 0
  const set = (key: string, v: unknown) => {
    setNotice(null)
    setAnswers(() => {
      const next = { ...current }
      if (v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)) delete next[key]
      else next[key] = v
      return next
    })
  }
  const newer = r && r.latest_form_version && r.latest_form_version > r.form_version ? r.latest_form_version : null

  if (isNew && !formKey) return <div className="muted">Pick a form on the <Link to="/records">Records</Link> page first.</div>

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="muted small"><Link to={`/records${r ? `?form=${r.form.key}` : formKey ? `?form=${formKey}` : ''}`}>Records</Link></div>
          <h1>
            {isNew ? `New ${definition.data?.form.name ?? ''} record` : r?.title ?? 'Record'}
            {r && (r.complete ? <span className="pill approved">complete</span> : <span className="pill warn">incomplete</span>)}
            {r?.status === 'archived' && <span className="pill gray">archived</span>}
          </h1>
          {r && <div className="muted small">{r.form.name} v{r.form_version} · revision {r.revision} · updated {formatDate(r.updated_at)}{r.updated_by_name ? ` by ${r.updated_by_name}` : ''}</div>}
        </div>
        {r && can('records:write') && (
          r.status === 'active'
            ? <button disabled={setStatus.isPending || dirty} onClick={() => setStatus.mutate('archive')}>Archive</button>
            : <button className="primary" disabled={setStatus.isPending} onClick={() => setStatus.mutate('restore')}>Restore</button>
        )}
      </div>
      <ErrorBox error={record.error ?? definition.error} />
      {newer && r?.status === 'active' && (
        <div className="notice warn">
          A newer version of this form exists (v{newer}). This record was saved with v{r.form_version}.
          {!readOnly && (
            <button disabled={save.isPending} onClick={() => save.mutate({ answers: diff, form_version: 'latest' })}>
              Move to v{newer}{dirty ? ' and save changes' : ''}
            </button>
          )}
        </div>
      )}
      {notice && <div className="notice">{notice}</div>}
      {r?.status === 'archived' && <div className="notice subtle">This record is archived. Restore it to edit.</div>}
      <div className="two-col">
        <div>
          {definition.data && (
            <div className="card preview">
              <FormFields definition={definition.data} answers={current} check={check.data} errors={errors} readOnly={readOnly} onChange={set} />
              <ErrorBox error={mutationError} />
              {!readOnly && (
                <div className="actions">
                  {isNew ? (
                    <button className="primary" disabled={create.isPending || blocked} onClick={() => create.mutate()}>Save record</button>
                  ) : (
                    <button className="primary" disabled={save.isPending || blocked || !dirty} onClick={() => save.mutate({ answers: diff })}>Save changes</button>
                  )}
                  {!isNew && dirty && <button onClick={() => setAnswers(null)}>Discard changes</button>}
                  {blocked && <span className="field-error">Fix the answers marked in red to save.</span>}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="side">
          <div className="card">
            <h3>Still needed</h3>
            {missing.length === 0
              ? <div className="ok">✓ Every required question is answered.</div>
              : (
                <>
                  <div className="muted small">You can save now and fill these in later. The record shows as incomplete until then.</div>
                  <ul className="plain">{missing.map((m) => <li key={m.field}>{labels.get(m.field) ?? m.field}</li>)}</ul>
                </>
              )}
          </div>
          {id && <History id={id} />}
        </div>
      </div>
    </div>
  )
}
