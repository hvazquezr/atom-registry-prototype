import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type ChangeRequest, type Field, type RelationType } from '../api'
import { useAuth } from '../auth'
import ErrorBox from '../components/ErrorBox'
import ValueView from '../components/ValueView'
import { DiffSummary, IssueList } from './FormBuilder'
import { kindLabel } from './ReviewQueue'
import { formatDate, useRelationTypes, useSchema } from '../hooks'

export function ChangeSummary({ cr, fields, types }: { cr: ChangeRequest; fields: Field[]; types: RelationType[] }) {
  const byKey = new Map(fields.map((f) => [f.key, f]))
  const keys = Object.keys(cr.proposed_values)
  const adds = cr.proposed_relations.add ?? []
  const removed = cr.relations_removed ?? []
  const typeLabel = (key: string, direction: string) => {
    const t = types.find((x) => x.key === key)
    return direction === 'in' ? t?.inverse_label || t?.label || key : t?.label || key
  }
  return (
    <div>
      {(cr.kind === 'deprecate' || cr.kind === 'restore') && (
        <p>{cr.kind === 'deprecate' ? 'Mark this atom as deprecated.' : 'Make this atom active again.'}</p>
      )}
      {keys.length > 0 && (
        <table className="table diff">
          <thead>
            <tr><th>Field</th>{cr.kind === 'update' && <th>Current</th>}<th>{cr.kind === 'update' ? 'Proposed' : 'Value'}</th></tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k}>
                <td>{byKey.get(k)?.label ?? k}</td>
                {cr.kind === 'update' && (
                  <td className="old"><ValueView field={byKey.get(k)} value={cr.current_values?.[k]} /></td>
                )}
                <td className="new"><ValueView field={byKey.get(k)} value={cr.proposed_values[k]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(adds.length > 0 || removed.length > 0) && (
        <div className="rel-changes">
          <h4>Relations</h4>
          <ul>
            {adds.map((r, i) => (
              <li key={i} className="added">
                + {typeLabel(r.type, r.direction)}{' '}
                <Link to={`/atoms/${r.target_atom_id}`}>{cr.relation_targets?.[r.target_atom_id] ?? r.target_atom_id}</Link>
                {r.note && <span className="muted"> — {r.note}</span>}
              </li>
            ))}
            {removed.map((r) => (
              <li key={r.id} className="removed">
                − {r.label} <Link to={`/atoms/${r.other_atom_id}`}>{r.other_atom_name ?? r.other_atom_id}</Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function FormRequestSummary({ cr }: { cr: ChangeRequest }) {
  const p = cr.form_preview
  if (cr.kind === 'deprecate_form') return <p>Mark this form as deprecated. Published versions stay readable.</p>
  if (cr.kind === 'restore_form') return <p>Make this form active again.</p>
  if (!p) return null
  const items = p.definition.sections.flatMap((s) => s.items)
  return (
    <div>
      <p>
        Publish <strong>v{p.version}</strong> of {p.definition.form.name} with {items.length} atoms in {p.definition.sections.length}{' '}
        {p.definition.sections.length === 1 ? 'section' : 'sections'}.
        {cr.proposed_values.notes ? <> Notes: “{String(cr.proposed_values.notes)}”</> : null}
      </p>
      <DiffSummary diff={p.diff} since={p.version > 1 ? `v${p.version - 1}` : undefined} />
      {cr.status === 'pending' && (
        <>
          <h4>Checks</h4>
          <IssueList issues={p.issues} />
        </>
      )}
      <h4>Questions</h4>
      {p.definition.sections.map((s, i) => (
        <div key={i}>
          {s.title && <div className="strong small">{s.title}</div>}
          <ol className="small">
            {s.items.map((it) => (
              <li key={it.key}>
                {it.label} <span className="muted mono">{it.key}</span> <span className="pill gray">{it.input.answer_type}</span>
                {it.required && <span className="pill blue">required</span>}
                {it.conditions.length > 0 && (
                  <span className="muted"> · required when {it.conditions.map((c) => (c.condition ? `${c.atom_name}: ${c.condition}` : `${c.atom_name} is answered`)).join(' or ')}</span>
                )}
                <span className="muted"> · atom rev {it.atom_revision}</span>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  )
}

export default function ChangeRequestPage() {
  const { id } = useParams()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { me, can } = useAuth()
  const [reviewComment, setReviewComment] = useState('')
  const schema = useSchema()
  const types = useRelationTypes()
  const cr = useQuery({ queryKey: ['change-request', id], queryFn: () => api<ChangeRequest>(`/api/v1/change-requests/${id}`) })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['change-request', id] })
    qc.invalidateQueries({ queryKey: ['review-queue'] })
    qc.invalidateQueries({ queryKey: ['atoms'] })
    qc.invalidateQueries({ queryKey: ['atom'] })
    qc.invalidateQueries({ queryKey: ['relations'] })
    qc.invalidateQueries({ queryKey: ['form'] })
    qc.invalidateQueries({ queryKey: ['forms'] })
  }
  const approve = useMutation({
    mutationFn: () => api<{ atom: { id: string } }>(`/internal/change-requests/${id}/approve`, { body: { comment: reviewComment } }),
    onSuccess: refresh,
  })
  const reject = useMutation({
    mutationFn: () => api(`/internal/change-requests/${id}/reject`, { body: { comment: reviewComment } }),
    onSuccess: refresh,
  })
  const withdraw = useMutation({ mutationFn: () => api(`/api/v1/change-requests/${id}/withdraw`, { method: 'POST' }), onSuccess: refresh })
  const rebase = useMutation({
    mutationFn: () => api(`/api/v1/change-requests/${id}`, { method: 'PATCH', body: {} }),
    onSuccess: refresh,
  })

  if (cr.error) return <ErrorBox error={cr.error} />
  if (!cr.data) return <div className="muted">Loading…</div>
  const c = cr.data
  const mine = c.submitted_by === me?.id
  const pending = c.status === 'pending'

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="muted small"><Link to={can('atoms:approve') ? '/review' : '/requests'}>Change requests</Link> /</div>
          <h1>
            {kindLabel(c.kind)[0].toUpperCase() + kindLabel(c.kind).slice(1)}{' '}
            {c.form_id ? (
              <>form <Link to={`/forms/${c.form_id}`}>{c.form_name ?? 'form'}</Link></>
            ) : c.atom_id ? <Link to={`/atoms/${c.atom_id}`}>{c.atom_name ?? 'atom'}</Link> : c.atom_name ?? 'new atom'}{' '}
            <span className={`pill ${c.status}`}>{c.status}</span>
          </h1>
          <div className="muted small">
            Submitted by {c.submitted_by_name} · {formatDate(c.submitted_at)}
            {c.base_revision && ` · based on ${c.form_id ? 'draft ' : ''}revision ${c.base_revision}`}
          </div>
        </div>
      </div>
      {c.comment && <blockquote className="comment">{c.comment}</blockquote>}
      {c.stale && (
        <div className="notice warn">
          The {c.form_id ? "form's draft" : 'atom'} changed after this request was submitted (now revision {c.current_revision}). {mine ? 'Check the values below, then update the request.' : 'The submitter needs to update it before it can be approved.'}
          {mine && <button onClick={() => rebase.mutate()}>Update to the current revision</button>}
        </div>
      )}
      <div className="card">
        {c.form_id ? <FormRequestSummary cr={c} /> : <ChangeSummary cr={c} fields={schema.data?.fields ?? []} types={types.data ?? []} />}
      </div>
      {c.reviewed_by_name && (
        <div className="card">
          <strong>{c.status === 'approved' ? 'Approved' : 'Rejected'}</strong> by {c.reviewed_by_name} · {formatDate(c.reviewed_at)}
          {c.review_comment && <p>{c.review_comment}</p>}
        </div>
      )}
      <ErrorBox error={approve.error ?? reject.error ?? withdraw.error ?? rebase.error} />
      {pending && can('atoms:approve') && !mine && (
        <div className="card">
          <h3>Review</h3>
          <textarea rows={2} placeholder="Comment (optional for approval, helpful for rejection)" value={reviewComment} onChange={(e) => setReviewComment(e.target.value)} />
          <div className="actions">
            <button className="primary" disabled={approve.isPending || c.stale} onClick={() => approve.mutate()}>Approve</button>
            <button className="danger" disabled={reject.isPending} onClick={() => reject.mutate()}>Reject</button>
          </div>
        </div>
      )}
      {pending && mine && (
        <div className="actions">
          <span className="muted small">Someone else with the reviewer role needs to approve this.</span>
          <button onClick={() => withdraw.mutate()}>Withdraw</button>
        </div>
      )}
      {c.form_id && <button onClick={() => navigate(`/forms/${c.form_id}`)}>Open form</button>}
      {c.status === 'approved' && c.atom_id && (
        <button onClick={() => navigate(`/atoms/${c.atom_id}`)}>Open atom</button>
      )}
    </div>
  )
}
