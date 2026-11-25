import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, atomName, type Atom, type ChangeRequest, type FormSummary, type Page, type Relation, type RelationType, type Revision, type Values } from '../api'
import { useAuth } from '../auth'
import AtomPicker from '../components/AtomPicker'
import FieldInput from '../components/FieldInput'
import ErrorBox from '../components/ErrorBox'
import Graph from '../components/Graph'
import ValueView from '../components/ValueView'
import { formatDate, useRelationTypes, useSchema } from '../hooks'

type Tab = 'details' | 'relations' | 'graph' | 'history' | 'requests'

function AddRelation({ atom, types, onDone }: { atom: Atom; types: RelationType[]; onDone: (cr: ChangeRequest) => void }) {
  const active = types.filter((t) => t.status === 'active')
  const [choice, setChoice] = useState(active[0] ? `${active[0].key}:out` : '')
  const [target, setTarget] = useState<Atom | null>(null)
  const [note, setNote] = useState('')
  const [comment, setComment] = useState('')
  const [attributes, setAttributes] = useState<Values>({})
  const [typeKey, direction] = choice.split(':') as [string, 'out' | 'in']
  const type = active.find((t) => t.key === typeKey)
  const submit = useMutation({
    mutationFn: () =>
      api<ChangeRequest>(`/api/v1/atoms/${atom.id}`, {
        method: 'PATCH',
        body: { relations: { add: [{ type: typeKey, target_atom_id: target!.id, direction, note, attributes }] }, comment, base_revision: atom.revision },
      }),
    onSuccess: onDone,
  })
  return (
    <div className="card inset">
      <h3>Add a relation</h3>
      <div className="relation-sentence">
        <strong>{atomName(atom.values)}</strong>
        <select value={choice} onChange={(e) => (setChoice(e.target.value), setAttributes({}))}>
          {active.map((t) => [
            <option key={`${t.key}:out`} value={`${t.key}:out`}>{t.label.toLowerCase()}</option>,
            t.directed && t.inverse_label ? (
              <option key={`${t.key}:in`} value={`${t.key}:in`}>{t.inverse_label.toLowerCase()}</option>
            ) : null,
          ])}
        </select>
        {target ? (
          <span className="chip">
            {atomName(target.values)}
            <button className="chip-x" onClick={() => setTarget(null)}>×</button>
          </span>
        ) : (
          <AtomPicker exclude={[atom.id]} onPick={setTarget} />
        )}
      </div>
      {type?.description && <div className="muted small">{type.description}</div>}
      <div className="form-field">
        <label>Note{type?.require_note && <span className="req"> *</span>}</label>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why are these atoms related?" />
      </div>
      {(type?.attributes ?? []).map((f) => (
        <FieldInput key={f.key} field={{ ...f, expression: null, description: f.description ?? '', calculated: false, config: f.config ?? {} }}
          value={attributes[f.key]} onChange={(v) => setAttributes((a) => ({ ...a, [f.key]: v }))} />
      ))}
      {type?.key === 'required_if' && (
        <div className="muted small">
          The condition is checked against the other atom's answer on a form, for example <code>value == true</code> or{' '}
          <code>value &gt; 18</code>. In “{atomName(atom.values)} required if X”, X is the other atom.
        </div>
      )}
      <div className="form-field">
        <label>Comment for the reviewer</label>
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </div>
      <ErrorBox error={submit.error} />
      <button className="primary" disabled={!target || submit.isPending} onClick={() => submit.mutate()}>
        Submit for review
      </button>
    </div>
  )
}

export default function AtomDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { can } = useAuth()
  const [tab, setTab] = useState<Tab>('details')
  const [adding, setAdding] = useState(false)
  const [depth, setDepth] = useState(2)
  const [notice, setNotice] = useState<ChangeRequest | null>(null)
  const schema = useSchema()
  const types = useRelationTypes()

  const atom = useQuery({ queryKey: ['atom', id], queryFn: () => api<Atom>(`/api/v1/atoms/${id}`) })
  const usedIn = useQuery({
    queryKey: ['forms', 'atom', id],
    queryFn: () => api<Page<FormSummary>>(`/api/v1/forms?atom_id=${id}&status=all`),
    enabled: can('forms:read'),
  })
  const relations = useQuery({ queryKey: ['relations', id], queryFn: () => api<Relation[]>(`/api/v1/atoms/${id}/relations`) })
  const history = useQuery({ queryKey: ['revisions', id], queryFn: () => api<Revision[]>(`/api/v1/atoms/${id}/revisions`), enabled: tab === 'history' })
  const requests = useQuery({ queryKey: ['atom-requests', id], queryFn: () => api<ChangeRequest[]>(`/api/v1/change-requests?atom_id=${id}`), enabled: tab === 'requests' })
  const graph = useQuery({
    queryKey: ['graph', id, depth],
    queryFn: () => api<Parameters<typeof Graph>[0] & { root: string }>(`/api/v1/atoms/${id}/graph?depth=${depth}`),
    enabled: tab === 'graph',
  })

  const submitted = (cr: ChangeRequest) => {
    setNotice(cr)
    setAdding(false)
    qc.invalidateQueries({ queryKey: ['atom', id] })
    qc.invalidateQueries({ queryKey: ['atom-requests', id] })
    qc.invalidateQueries({ queryKey: ['review-queue'] })
  }
  const statusChange = useMutation({
    mutationFn: (kind: 'deprecate' | 'restore') =>
      api<ChangeRequest>(`/api/v1/atoms/${id}/${kind}`, { body: { base_revision: atom.data?.revision } }),
    onSuccess: submitted,
  })
  const removeRelation = useMutation({
    mutationFn: (rel: Relation) =>
      api<ChangeRequest>(`/api/v1/atoms/${id}`, { method: 'PATCH', body: { relations: { remove: [rel.id] }, base_revision: atom.data?.revision } }),
    onSuccess: submitted,
  })

  if (atom.error) return <ErrorBox error={atom.error} />
  if (!atom.data) return <div className="muted">Loading…</div>
  const a = atom.data
  const fields = schema.data?.fields ?? []
  const legacy = Object.keys(a.values).filter((k) => !fields.some((f) => f.key === k))
  const pending = a.pending_change_requests ?? 0

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="muted small"><Link to="/atoms">Atoms</Link> /</div>
          <h1>
            {atomName(a.values)} {a.status === 'deprecated' && <span className="pill gray">deprecated</span>}
          </h1>
          <div className="muted small">
            Revision {a.revision} · schema v{a.schema_version} · updated {formatDate(a.updated_at)}
          </div>
        </div>
        {can('atoms:write') && (
          <div className="actions">
            {a.status === 'active' && <button onClick={() => navigate(`/atoms/${a.id}/edit`)}>Edit</button>}
            {a.status === 'active' ? (
              <button className="danger" onClick={() => statusChange.mutate('deprecate')}>Deprecate</button>
            ) : (
              <button onClick={() => statusChange.mutate('restore')}>Restore</button>
            )}
          </div>
        )}
      </div>

      {notice && (
        <div className="notice">
          Your change was submitted for review. <Link to={`/requests/${notice.id}`}>View request</Link>
        </div>
      )}
      {pending > 0 && !notice && (
        <div className="notice subtle">
          {pending} pending change {pending === 1 ? 'request' : 'requests'}.{' '}
          <button className="link" onClick={() => setTab('requests')}>See them</button>
        </div>
      )}
      <ErrorBox error={statusChange.error ?? removeRelation.error} />

      <div className="tabs">
        {(['details', 'relations', 'graph', 'history', 'requests'] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
            {t === 'relations' && relations.data ? ` (${relations.data.length})` : ''}
          </button>
        ))}
      </div>

      {tab === 'details' && (
        <div className="card">
          <dl className="values">
            {fields.map((f) => (
              <div key={f.key} className="value-row">
                <dt>
                  {f.label}
                  {f.calculated && <span className="pill blue" title={f.expression ?? ''}>calculated</span>}
                </dt>
                <dd><ValueView field={f} value={a.values[f.key]} /></dd>
              </div>
            ))}
            {(usedIn.data?.items.length ?? 0) > 0 && (
              <div className="value-row">
                <dt>Used in forms</dt>
                <dd>
                  {usedIn.data!.items.map((f, i) => (
                    <span key={f.id}>{i > 0 && ', '}<Link to={`/forms/${f.id}`}>{f.name}</Link></span>
                  ))}
                </dd>
              </div>
            )}
            {legacy.map((k) => (
              <div key={k} className="value-row">
                <dt>{k} <span className="pill gray">deprecated field</span></dt>
                <dd><ValueView value={a.values[k]} /></dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {tab === 'relations' && (
        <div>
          {can('atoms:write') && a.status === 'active' && !adding && (
            <button className="primary" onClick={() => setAdding(true)}>Add relation</button>
          )}
          {adding && types.data && <AddRelation atom={a} types={types.data} onDone={submitted} />}
          <div className="card flush">
            <table className="table">
              <thead>
                <tr><th>Relation</th><th>Atom</th><th>Note</th><th /></tr>
              </thead>
              <tbody>
                {relations.data?.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">{r.label}</td>
                    <td><Link to={`/atoms/${r.other_atom_id}`}>{r.other_atom_name ?? r.other_atom_id}</Link></td>
                    <td className="muted">
                      {r.note}
                      {Object.entries(r.attributes ?? {}).map(([k, v]) => (
                        <div key={k} className="small"><span className="muted">{k}:</span> <code>{String(v)}</code></div>
                      ))}
                    </td>
                    <td className="right">
                      {can('atoms:write') && a.status === 'active' && (
                        <button className="link danger" onClick={() => removeRelation.mutate(r)}>Remove</button>
                      )}
                    </td>
                  </tr>
                ))}
                {relations.data?.length === 0 && <tr><td colSpan={4} className="muted center">No relations yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'graph' && (
        <div className="card">
          <div className="toolbar">
            <label className="muted">Depth</label>
            <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
              {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{d} {d === 1 ? 'hop' : 'hops'}</option>)}
            </select>
            <span className="muted small">Click an atom to open it. Hover a line to see its note.</span>
          </div>
          <ErrorBox error={graph.error} />
          {graph.data && <Graph root={a.id} nodes={graph.data.nodes} edges={graph.data.edges} />}
        </div>
      )}

      {tab === 'history' && (
        <div className="timeline">
          {history.data?.map((r) => (
            <div key={r.revision} className="card timeline-item">
              <div className="row spread">
                <strong>Revision {r.revision}: {r.summary || 'Change'}</strong>
                <span className="muted small">{r.changed_by_name} · {formatDate(r.changed_at)}</span>
              </div>
              <details>
                <summary className="muted small">Values at this revision</summary>
                <dl className="values compact">
                  {fields.filter((f) => r.values[f.key] !== undefined).map((f) => (
                    <div key={f.key} className="value-row">
                      <dt>{f.label}</dt>
                      <dd><ValueView field={f} value={r.values[f.key]} /></dd>
                    </div>
                  ))}
                </dl>
                {r.relations.length > 0 && (
                  <div className="small">
                    Relations: {r.relations.map((x) => `${x.label} ${x.other_atom_id.slice(0, 8)}`).join(', ')}
                  </div>
                )}
              </details>
            </div>
          ))}
        </div>
      )}

      {tab === 'requests' && (
        <div className="card flush">
          <table className="table">
            <thead><tr><th>Request</th><th>Status</th><th>Submitted</th><th>Reviewed</th></tr></thead>
            <tbody>
              {requests.data?.map((cr) => (
                <tr key={cr.id}>
                  <td><Link to={`/requests/${cr.id}`}>{cr.kind}</Link> {cr.comment && <span className="muted small">— {cr.comment}</span>}</td>
                  <td><span className={`pill ${cr.status}`}>{cr.status}</span></td>
                  <td className="small">{cr.submitted_by_name} · {formatDate(cr.submitted_at)}</td>
                  <td className="small">{cr.reviewed_by_name ? `${cr.reviewed_by_name} · ${formatDate(cr.reviewed_at)}` : ''}</td>
                </tr>
              ))}
              {requests.data?.length === 0 && <tr><td colSpan={4} className="muted center">No change requests.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
