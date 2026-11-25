import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  ApiError,
  atomName,
  type AnswerCheck,
  type ChangeRequest,
  type DraftItem,
  type DraftSection,
  type FormDefinition,
  type FormDetail,
  type FormDiff,
  type FormIssue,
  type ResolvedItem,
  type Values,
} from '../api'
import { useAuth } from '../auth'
import AtomPicker from '../components/AtomPicker'
import ErrorBox from '../components/ErrorBox'
import FormFields from '../components/FormFields'
import { formatDate } from '../hooks'

type Tab = 'build' | 'preview' | 'versions' | 'requests'

interface Draft {
  name: string
  description: string
  sections: DraftSection[]
}

const ANSWER_LABELS: Record<string, string> = {
  text: 'Text', integer: 'Whole number', number: 'Number', date: 'Date', boolean: 'Yes/No', choice: 'Choice', multiple_choice: 'Choices',
}

function toDraft(f: FormDetail): Draft {
  return { name: f.name, description: f.description, sections: f.draft.sections }
}

function conditionText(c: { atom_name: string | null; condition: string | null }) {
  return c.condition ? `${c.atom_name}: ${c.condition}` : `${c.atom_name} is answered`
}

export function DiffSummary({ diff, since }: { diff: FormDiff; since?: string }) {
  const parts = [
    ...diff.added.map((a) => <li key={`a${a.key}`} className="added">+ {a.label}</li>),
    ...diff.removed.map((a) => <li key={`r${a.key}`} className="removed">− {a.label}</li>),
    ...diff.changed.map((c) => <li key={`c${c.key}`}>~ {c.label}: <span className="muted">{c.changes.join(', ')}</span></li>),
  ]
  if (diff.sections_changed) parts.push(<li key="s">~ Sections changed</li>)
  if (diff.details_changed) parts.push(<li key="d">~ Name or description changed</li>)
  if (!parts.length) return <div className="muted small">No changes{since ? ` since ${since}` : ''}.</div>
  return (
    <div className="rel-changes">
      {since && <h4>Changes since {since}</h4>}
      <ul>{parts}</ul>
    </div>
  )
}

export function IssueList({ issues, onAdd }: { issues: FormIssue[]; onAdd?: (i: FormIssue) => void }) {
  if (!issues.length) return <div className="ok small">✓ Ready to publish.</div>
  const sorted = [...issues].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'error' ? -1 : 1))
  return (
    <ul className="issues">
      {sorted.map((i, n) => (
        <li key={n} className={i.severity}>
          <span className={`pill ${i.severity === 'error' ? 'rejected' : 'warn'}`}>{i.severity === 'error' ? 'Blocks publishing' : 'Warning'}</span>
          <div>{i.message}</div>
          {onAdd && i.add_atom_id && (
            <button className="link" onClick={() => onAdd(i)}>Add {i.add_atom_name}</button>
          )}
        </li>
      ))}
    </ul>
  )
}

// --- Build --------------------------------------------------------------------------------

const KEY_INPUTS = new Set(['text', 'integer', 'number', 'date', 'choice'])

function keyableInput(resolved?: ResolvedItem) {
  return !resolved || KEY_INPUTS.has(resolved.input.type)
}

function ItemRow({
  item, resolved, name, editable, onChange, onRemove, onMove, dragProps,
}: {
  item: DraftItem
  resolved?: ResolvedItem
  name: string
  editable: boolean
  onChange: (patch: Partial<DraftItem>, save: boolean) => void
  onRemove: () => void
  onMove: (delta: number) => void
  dragProps: React.HTMLAttributes<HTMLDivElement>
}) {
  const [open, setOpen] = useState(false)
  const conditional = (resolved?.conditions.length ?? 0) > 0
  const calc = resolved?.calculation
  return (
    <div className={`form-item ${resolved?.parent ? 'child' : ''} ${resolved?.atom_status === 'deprecated' ? 'deprecated' : ''}`} {...dragProps}>
      <div className="row spread">
        <div className="row">
          {editable && <span className="grip" title="Drag to reorder">⋮⋮</span>}
          <div>
            <div>
              <strong>{item.label || name}</strong>
              {item.label && <span className="muted small"> ({name})</span>}
              {resolved && <span className="pill gray">{ANSWER_LABELS[resolved.input.answer_type] ?? resolved.input.answer_type}</span>}
              {calc && <span className="pill blue" title={calc.formula}>calculated</span>}
              {item.natural_key && <span className="pill key" title="Part of the natural key of this form's records">key</span>}
              {resolved?.atom_status === 'deprecated' && <span className="pill rejected">deprecated atom</span>}
            </div>
            <div className="muted small">
              <span className="mono">{item.key ?? '…'}</span>
              {resolved?.parent && <> · grouped under <span className="mono">{resolved.parent}</span></>}
              {resolved && resolved.conditions.length > 0 && <> · shown and required when {resolved.conditions.map(conditionText).join(' or ')}</>}
              {resolved && resolved.depends_on.length > 0 && <> · depends on {resolved.depends_on.join(', ')}</>}
              {calc && <> · calculated from {calc.inputs.length ? calc.inputs.map((i) => i.key).join(', ') : 'atoms not on the form yet'}</>}
            </div>
          </div>
        </div>
        <div className="row">
          {calc ? (
            <span className="requirement locked" title="Calculated answers are never asked, so they're neither required nor optional.">Calculated</span>
          ) : conditional ? (
            <span className="requirement locked" title="Set by the atom's Required if link.">Required if</span>
          ) : item.natural_key ? (
            <span className="requirement req" title="Natural key questions are always required.">Required</span>
          ) : editable ? (
            <div className="segmented" role="group" aria-label="Required or optional">
              <button className={item.required ? 'on' : ''} aria-pressed={item.required} onClick={() => !item.required && onChange({ required: true }, true)}>Required</button>
              <button className={!item.required ? 'on' : ''} aria-pressed={!item.required} onClick={() => item.required && onChange({ required: false }, true)}>Optional</button>
            </div>
          ) : (
            <span className={`requirement ${item.required ? 'req' : ''}`}>{item.required ? 'Required' : 'Optional'}</span>
          )}
          {editable && (item.natural_key || (!calc && !conditional && keyableInput(resolved))) && (
            <label className="check small" title="Identifies a record: no two records of this form can share it, and the API can get a record by it.">
              <input type="checkbox" checked={!!item.natural_key} onChange={(e) => onChange({ natural_key: e.target.checked }, true)} /> Key
            </label>
          )}
          <Link to={`/atoms/${item.atom_id}`} className="small">Atom</Link>
          {editable && (
            <>
              <button className="link" onClick={() => setOpen(!open)}>{open ? 'Close' : 'Edit'}</button>
              <button className="icon" title="Move up" onClick={() => onMove(-1)}>↑</button>
              <button className="icon" title="Move down" onClick={() => onMove(1)}>↓</button>
              <button className="link danger" onClick={onRemove}>Remove</button>
            </>
          )}
        </div>
      </div>
      {open && editable && (
        <div className="item-edit">
          <div className="grid2">
            <div className="form-field">
              <label>Label on this form</label>
              <input value={item.label} placeholder={name} onChange={(e) => onChange({ label: e.target.value }, false)} onBlur={() => onChange({}, true)} />
            </div>
            <div className="form-field">
              <label>Answer key</label>
              <input className="mono" value={item.key ?? ''} onChange={(e) => onChange({ key: e.target.value }, false)} onBlur={() => onChange({}, true)} />
            </div>
          </div>
          <div className="form-field">
            <label>Help text</label>
            <textarea rows={2} value={item.help_text} placeholder={String(resolved?.atom_values.description ?? '')}
              onChange={(e) => onChange({ help_text: e.target.value }, false)} onBlur={() => onChange({}, true)} />
          </div>
        </div>
      )}
    </div>
  )
}

function Builder({ form, editable, onSaved }: { form: FormDetail; editable: boolean; onSaved: (f: FormDetail) => void }) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(form))
  const [names, setNames] = useState<Record<string, string>>({})
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const revision = useRef(form.draft_revision)
  const queued = useRef<Draft | null>(null)
  const drag = useRef<{ si: number; ii: number } | null>(null)

  // Pick up changes saved elsewhere (e.g. another tab) when we have nothing unsaved.
  useEffect(() => {
    if (!saving && !queued.current && form.draft_revision !== revision.current) {
      revision.current = form.draft_revision
      setDraft(toDraft(form))
    }
  }, [form, saving])

  const resolvedByAtom = useMemo(() => {
    const m = new Map<string, ResolvedItem>()
    for (const s of form.resolved_draft.sections) for (const i of s.items) m.set(i.atom_id, i)
    return m
  }, [form])
  const nameOf = (atomId: string) => names[atomId] ?? resolvedByAtom.get(atomId)?.atom_name ?? '…'
  const onForm = new Set(draft.sections.flatMap((s) => s.items.map((i) => i.atom_id)))

  const flush = async () => {
    if (saving) return
    setSaving(true)
    try {
      while (queued.current) {
        const body = queued.current
        queued.current = null
        const f = await api<FormDetail>(`/api/v1/forms/${form.id}/draft`, {
          method: 'PUT',
          body: { ...body, base_revision: revision.current },
        })
        revision.current = f.draft_revision
        setError(null)
        setDirty(false)
        onSaved(f)
        if (!queued.current) setDraft(toDraft(f))
      }
    } catch (e) {
      queued.current = null
      setError(e)
      if (e instanceof ApiError && e.code === 'stale') onSaved(form)
    } finally {
      setSaving(false)
    }
  }

  const commit = (next: Draft, save = true) => {
    setDraft(next)
    setDirty(true)
    if (save) {
      queued.current = next
      void flush()
    }
  }
  const saveNow = () => {
    if (!dirty) return
    queued.current = draft
    void flush()
  }

  const updateSections = (fn: (s: DraftSection[]) => DraftSection[], save = true) =>
    commit({ ...draft, sections: fn(draft.sections.map((s) => ({ ...s, items: [...s.items] }))) }, save)

  const addAtom = (si: number, atomId: string, name: string, index?: number) => {
    setNames((n) => ({ ...n, [atomId]: name }))
    updateSections((ss) => {
      const item: DraftItem = { atom_id: atomId, key: null, label: '', help_text: '', required: false }
      const items = ss[si].items
      items.splice(index ?? items.length, 0, item)
      return ss
    })
  }

  const addFromIssue = (issue: FormIssue) => {
    if (!issue.add_atom_id) return
    let si = draft.sections.length - 1
    let ii: number | undefined
    draft.sections.forEach((s, sIdx) => s.items.forEach((it, iIdx) => {
      if (it.key === issue.item) {
        si = sIdx
        ii = issue.code === 'missing_included' ? iIdx + 1 : iIdx
      }
    }))
    addAtom(si, issue.add_atom_id, issue.add_atom_name ?? '…', ii)
  }

  const move = (si: number, ii: number, toSi: number, toIi: number) =>
    updateSections((ss) => {
      const [it] = ss[si].items.splice(ii, 1)
      const adjusted = si === toSi && toIi > ii ? toIi - 1 : toIi
      ss[toSi].items.splice(Math.max(0, Math.min(adjusted, ss[toSi].items.length)), 0, it)
      return ss
    })

  const moveBy = (si: number, ii: number, delta: number) => {
    const target = ii + delta
    if (target < 0) {
      if (si > 0) move(si, ii, si - 1, draft.sections[si - 1].items.length)
    } else if (target >= draft.sections[si].items.length) {
      if (si < draft.sections.length - 1) move(si, ii, si + 1, 0)
    } else {
      move(si, ii, si, delta > 0 ? target + 1 : target)
    }
  }

  const dropProps = (si: number, ii: number) => ({
    draggable: editable,
    onDragStart: (e: React.DragEvent) => {
      drag.current = { si, ii }
      e.dataTransfer.effectAllowed = 'move'
    },
    onDragOver: (e: React.DragEvent) => editable && e.preventDefault(),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (drag.current) move(drag.current.si, drag.current.ii, si, ii)
      drag.current = null
    },
  })

  const dropAtEnd = (si: number) => {
    if (drag.current) move(drag.current.si, drag.current.ii, si, draft.sections[si].items.length)
    drag.current = null
  }
  const unsaved = saving || dirty

  return (
    <div className="two-col">
      <div>
        <div className="card">
          <div className="form-field">
            <label>Name</label>
            <input value={draft.name} disabled={!editable} onChange={(e) => commit({ ...draft, name: e.target.value }, false)} onBlur={saveNow} />
          </div>
          <div className="form-field">
            <label>Description</label>
            <textarea rows={2} value={draft.description} disabled={!editable}
              onChange={(e) => commit({ ...draft, description: e.target.value }, false)} onBlur={saveNow} />
          </div>
        </div>
        {draft.sections.map((s, si) => (
          <div key={s.id ?? `new-${si}`} className="card form-section"
            onDragOver={(e) => editable && e.preventDefault()}
            onDrop={(e) => (e.preventDefault(), dropAtEnd(si))}>
            <div className="row spread">
              <input className="section-title grow" value={s.title} placeholder="Untitled section" disabled={!editable}
                onChange={(e) => updateSections((ss) => (ss[si] = { ...ss[si], title: e.target.value }, ss), false)} onBlur={saveNow} />
              {editable && (
                <div className="row">
                  <button className="icon" title="Move section up" disabled={si === 0}
                    onClick={() => updateSections((ss) => ([ss[si - 1], ss[si]] = [ss[si], ss[si - 1]], ss))}>↑</button>
                  <button className="icon" title="Move section down" disabled={si === draft.sections.length - 1}
                    onClick={() => updateSections((ss) => ([ss[si + 1], ss[si]] = [ss[si], ss[si + 1]], ss))}>↓</button>
                  <button className="link danger" disabled={s.items.length > 0 || draft.sections.length === 1}
                    title={s.items.length ? 'Move or remove its atoms first' : ''}
                    onClick={() => updateSections((ss) => ss.filter((_, j) => j !== si))}>Remove section</button>
                </div>
              )}
            </div>
            <input className="muted small section-desc" value={s.description} placeholder={editable ? 'Section description (optional)' : ''} disabled={!editable}
              onChange={(e) => updateSections((ss) => (ss[si] = { ...ss[si], description: e.target.value }, ss), false)} onBlur={saveNow} />
            {s.items.map((it, ii) => (
              <ItemRow key={it.atom_id} item={it} resolved={resolvedByAtom.get(it.atom_id)} name={nameOf(it.atom_id)} editable={editable}
                onChange={(patch, save) => {
                  if (!Object.keys(patch).length) return save && saveNow()
                  updateSections((ss) => (ss[si].items[ii] = { ...ss[si].items[ii], ...patch }, ss), save)
                }}
                onRemove={() => updateSections((ss) => (ss[si].items.splice(ii, 1), ss))}
                onMove={(d) => moveBy(si, ii, d)}
                dragProps={dropProps(si, ii)} />
            ))}
            {s.items.length === 0 && <div className="muted small drop-hint">{editable ? 'Add atoms here, or drag them in.' : 'No atoms.'}</div>}
            {editable && (
              <div className="add-atom">
                <AtomPicker exclude={[...onForm]} onPick={(a) => addAtom(si, a.id, atomName(a.values))} />
              </div>
            )}
          </div>
        ))}
        {editable && (
          <button onClick={() => updateSections((ss) => [...ss, { title: '', description: '', items: [] }])}>Add section</button>
        )}
      </div>
      <div className="side">
        <div className="card">
          <div className="row spread">
            <h3>Checks</h3>
            <span className="muted small">{unsaved ? 'Saving…' : `Draft saved · revision ${form.draft_revision}`}</span>
          </div>
          <ErrorBox error={error} />
          <IssueList issues={form.issues} onAdd={editable ? addFromIssue : undefined} />
        </div>
        {form.latest_version && (
          <div className="card">
            <DiffSummary diff={form.draft_changes} since={`v${form.latest_version}`} />
          </div>
        )}
        <PublishCard form={form} disabled={unsaved} />
      </div>
    </div>
  )
}

function PublishCard({ form, disabled }: { form: FormDetail; disabled: boolean }) {
  const qc = useQueryClient()
  const { can } = useAuth()
  const [notes, setNotes] = useState('')
  const submit = useMutation({
    mutationFn: () => api<ChangeRequest>(`/api/v1/forms/${form.id}/publish`, { body: { notes } }),
    onSuccess: () => {
      setNotes('')
      qc.invalidateQueries({ queryKey: ['form', form.id] })
      qc.invalidateQueries({ queryKey: ['review-queue'] })
    },
  })
  if (!can('forms:write') || form.status !== 'active') return null
  if (form.pending_request) {
    return (
      <div className="card">
        <h3>Waiting for review</h3>
        <p className="small">
          <Link to={`/requests/${form.pending_request.id}`}>A {form.pending_request.kind.replace('_form', '')} request</Link> was submitted by{' '}
          {form.pending_request.submitted_by_name}.
        </p>
        {form.pending_request.stale && <div className="notice warn small">The draft changed after it was submitted. Open the request to update it.</div>}
      </div>
    )
  }
  return (
    <div className="card">
      <h3>Publish v{(form.latest_version ?? 0) + 1}</h3>
      <textarea rows={2} placeholder="What changed? (shown in the version history)" value={notes} onChange={(e) => setNotes(e.target.value)} />
      <ErrorBox error={submit.error} />
      <button className="primary" disabled={!form.can_publish || disabled || submit.isPending} onClick={() => submit.mutate()}>
        Submit for review
      </button>
      {!form.can_publish && <div className="muted small">Fix the checks marked “Blocks publishing” first.</div>}
    </div>
  )
}

// --- Preview ------------------------------------------------------------------------------

function Preview({ form, version, setVersion }: { form: FormDetail; version: string; setVersion: (v: string) => void }) {
  const [answers, setAnswers] = useState<Values>({})
  const [submitted, setSubmitted] = useState(false)
  const [debounced, setDebounced] = useState<Values>({})
  useEffect(() => {
    const t = setTimeout(() => setDebounced(answers), 200)
    return () => clearTimeout(t)
  }, [answers])
  const definition = useQuery({
    queryKey: ['form-version', form.id, version, form.draft_revision],
    queryFn: () => api<FormDefinition>(`/api/v1/forms/${form.id}/versions/${version}`),
  })
  const check = useQuery({
    queryKey: ['form-validate', form.id, version, form.draft_revision, debounced],
    queryFn: () => api<AnswerCheck>(`/api/v1/forms/${form.id}/validate`, {
      body: { version: version === 'draft' ? 'draft' : Number(version), answers: debounced },
    }),
    placeholderData: keepPreviousData,
  })
  const errors: Record<string, string> = {}
  for (const e of check.data?.errors ?? []) {
    if (e.code === 'required' && !submitted) continue
    errors[e.field] = errors[e.field] ? `${errors[e.field]} ${e.message}` : e.message
  }
  const set = (key: string, v: unknown) => setAnswers((a) => {
    const next = { ...a }
    if (v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)) delete next[key]
    else next[key] = v
    return next
  })

  return (
    <div className="two-col">
      <div>
        <div className="toolbar">
          <label className="muted">Version</label>
          <select value={version} onChange={(e) => (setVersion(e.target.value), setAnswers({}), setSubmitted(false))}>
            <option value="draft">Current draft</option>
            {form.versions.map((v) => <option key={v.version} value={String(v.version)}>v{v.version}</option>)}
          </select>
          <button onClick={() => (setAnswers({}), setSubmitted(false))}>Clear answers</button>
        </div>
        <ErrorBox error={definition.error} />
        {definition.data && (
          <div className="card preview">
            <h2>{definition.data.form.name}</h2>
            {definition.data.form.description && <p className="muted">{definition.data.form.description}</p>}
            <FormFields definition={definition.data} answers={answers} check={check.data} errors={errors} onChange={set} />
            <button className="primary" onClick={() => setSubmitted(true)}>Check answers</button>
          </div>
        )}
      </div>
      <div className="side">
        <div className="card">
          <h3>Result</h3>
          {check.data && (
            <>
              {submitted && (check.data.valid ? <div className="ok">✓ These answers are valid.</div> : <div className="field-error">{check.data.errors.length} problem(s).</div>)}
              <div className="muted small">
                {check.data.fields.filter((f) => f.visible).length} of {check.data.fields.length} questions shown ·{' '}
                {check.data.fields.filter((f) => f.required).length} required now
              </div>
              {check.data.ignored.length > 0 && <div className="muted small">Ignored (hidden or calculated): {check.data.ignored.join(', ')}</div>}
              {check.data.fields.some((f) => f.calculated) && (
                <>
                  <h4>Calculated</h4>
                  <pre className="json">{JSON.stringify(Object.fromEntries(check.data.fields.filter((f) => f.calculated).map((f) => [f.key, f.value ?? null])), null, 2)}</pre>
                </>
              )}
              <h4>Answers sent to the API</h4>
              <pre className="json">{JSON.stringify({ version: version === 'draft' ? 'draft' : Number(version), answers }, null, 2)}</pre>
            </>
          )}
          <div className="muted small">
            This preview calls <code>POST /api/v1/forms/{form.key}/validate</code>, the same check other systems use.
          </div>
        </div>
      </div>
    </div>
  )
}

// --- Page ---------------------------------------------------------------------------------

export default function FormBuilder() {
  const { id } = useParams()
  const qc = useQueryClient()
  const { can } = useAuth()
  const [tab, setTab] = useState<Tab>('build')
  const [previewVersion, setPreviewVersion] = useState('draft')
  const [notice, setNotice] = useState<ChangeRequest | null>(null)
  const form = useQuery({ queryKey: ['form', id], queryFn: () => api<FormDetail>(`/api/v1/forms/${id}`) })
  const requests = useQuery({
    queryKey: ['form-requests', id],
    queryFn: () => api<ChangeRequest[]>(`/api/v1/forms/${id}/change-requests`),
    enabled: tab === 'requests',
  })
  const statusChange = useMutation({
    mutationFn: (kind: 'deprecate' | 'restore') => api<ChangeRequest>(`/api/v1/forms/${id}/${kind}`, { body: {} }),
    onSuccess: (cr) => {
      setNotice(cr)
      qc.invalidateQueries({ queryKey: ['form', id] })
      qc.invalidateQueries({ queryKey: ['review-queue'] })
    },
  })
  const republish = useMutation({
    mutationFn: () => api<ChangeRequest>(`/api/v1/forms/${id}/publish`, { body: { notes: 'Pick up updated atoms' } }),
    onSuccess: (cr) => {
      setNotice(cr)
      qc.invalidateQueries({ queryKey: ['form', id] })
      qc.invalidateQueries({ queryKey: ['review-queue'] })
    },
  })

  if (form.error) return <ErrorBox error={form.error} />
  if (!form.data) return <div className="muted">Loading…</div>
  const f = form.data
  const editable = can('forms:write') && f.status === 'active'
  const latest = f.versions[0]

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="muted small"><Link to="/forms">Forms</Link> /</div>
          <h1>
            {f.name}{' '}
            {f.status === 'deprecated' && <span className="pill gray">deprecated</span>}
            {latest ? <span className="pill approved">v{latest.version} published</span> : <span className="pill gray">not published</span>}
          </h1>
          <div className="muted small"><span className="mono">{f.key}</span> · draft revision {f.draft_revision} · updated {formatDate(f.updated_at)}</div>
        </div>
        {can('forms:write') && !f.pending_request && (
          <div className="actions">
            {f.status === 'active' ? (
              <button className="danger" onClick={() => statusChange.mutate('deprecate')}>Deprecate</button>
            ) : (
              <button onClick={() => statusChange.mutate('restore')}>Restore</button>
            )}
          </div>
        )}
      </div>
      {notice && (
        <div className="notice">Submitted for review. <Link to={`/requests/${notice.id}`}>View request</Link></div>
      )}
      {f.atoms_updated.length > 0 && latest && (
        <div className="notice warn">
          {f.atoms_updated.length === 1 ? '1 atom has' : `${f.atoms_updated.length} atoms have`} changed since v{latest.version}:{' '}
          {f.atoms_updated.map((a) => `${a.atom_name} (rev ${a.pinned_revision} → ${a.current_revision})`).join(', ')}.
          v{latest.version} keeps the old revisions.
          {can('forms:write') && f.can_publish && (
            <button onClick={() => republish.mutate()} disabled={republish.isPending}>Publish v{latest.version + 1} with the updates</button>
          )}
        </div>
      )}
      <ErrorBox error={statusChange.error ?? republish.error} />
      <div className="tabs">
        {(['build', 'preview', 'versions', 'requests'] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
            {t === 'versions' && f.versions.length ? ` (${f.versions.length})` : ''}
          </button>
        ))}
      </div>

      {tab === 'build' && (
        <Builder form={f} editable={editable} onSaved={(nf) => qc.setQueryData(['form', id], nf)} />
      )}
      {tab === 'preview' && <Preview form={f} version={previewVersion} setVersion={setPreviewVersion} />}
      {tab === 'versions' && (
        <div className="timeline">
          {f.versions.map((v) => (
            <div key={v.version} className="card timeline-item">
              <div className="row spread">
                <strong>v{v.version}{v.notes ? `: ${v.notes}` : ''}</strong>
                <span className="muted small">{formatDate(v.published_at)}</span>
              </div>
              <div className="muted small">
                {v.item_count} atoms · submitted by {v.published_by_name} · approved by {v.approved_by_name}
                {v.change_request_id && <> · <Link to={`/requests/${v.change_request_id}`}>request</Link></>}
              </div>
              <div className="row">
                <button className="link" onClick={() => (setPreviewVersion(String(v.version)), setTab('preview'))}>Preview</button>
              </div>
            </div>
          ))}
          {f.versions.length === 0 && <div className="muted">Not published yet.</div>}
        </div>
      )}
      {tab === 'requests' && (
        <div className="card flush">
          <table className="table">
            <thead><tr><th>Request</th><th>Status</th><th>Submitted</th><th>Reviewed</th></tr></thead>
            <tbody>
              {requests.data?.map((cr) => (
                <tr key={cr.id}>
                  <td><Link to={`/requests/${cr.id}`}>{cr.kind.replace('_form', '')}</Link> {cr.comment && <span className="muted small">— {cr.comment}</span>}</td>
                  <td><span className={`pill ${cr.status}`}>{cr.status}</span>{cr.stale && <span className="pill warn">out of date</span>}</td>
                  <td className="small">{cr.submitted_by_name} · {formatDate(cr.submitted_at)}</td>
                  <td className="small">{cr.reviewed_by_name ? `${cr.reviewed_by_name} · ${formatDate(cr.reviewed_at)}` : ''}</td>
                </tr>
              ))}
              {requests.data?.length === 0 && <tr><td colSpan={4} className="muted center">No requests yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
