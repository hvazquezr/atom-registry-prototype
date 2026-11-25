import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type ChangeRequest } from '../api'
import ErrorBox from '../components/ErrorBox'
import { formatDate } from '../hooks'

export function RequestTable({ rows, empty }: { rows: ChangeRequest[] | undefined; empty: string }) {
  return (
    <div className="card flush">
      <table className="table">
        <thead>
          <tr><th>Change</th><th>Atom</th><th>Submitted</th><th>Status</th></tr>
        </thead>
        <tbody>
          {rows?.map((cr) => (
            <tr key={cr.id}>
              <td>
                <Link to={`/requests/${cr.id}`} className="strong">{cr.kind}</Link>
                {Object.keys(cr.proposed_values).length > 0 && cr.kind === 'update' && (
                  <span className="muted small"> {Object.keys(cr.proposed_values).join(', ')}</span>
                )}
                {((cr.proposed_relations.add?.length ?? 0) + (cr.proposed_relations.remove?.length ?? 0)) > 0 && (
                  <span className="muted small"> relations</span>
                )}
                {cr.comment && <div className="muted small clamp">{cr.comment}</div>}
              </td>
              <td>{cr.atom_id ? <Link to={`/atoms/${cr.atom_id}`}>{cr.atom_name}</Link> : <span>{cr.atom_name} <span className="pill blue">new</span></span>}</td>
              <td className="small">{cr.submitted_by_name}<div className="muted">{formatDate(cr.submitted_at)}</div></td>
              <td>
                <span className={`pill ${cr.status}`}>{cr.status}</span>
                {cr.stale && <span className="pill warn">out of date</span>}
                {cr.can_review === false && <span className="muted small"> yours</span>}
              </td>
            </tr>
          ))}
          {rows?.length === 0 && <tr><td colSpan={4} className="muted center">{empty}</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function ReviewQueue() {
  const q = useQuery({ queryKey: ['review-queue'], queryFn: () => api<ChangeRequest[]>('/internal/review-queue') })
  return (
    <div>
      <div className="page-head">
        <h1>Review queue</h1>
      </div>
      <p className="muted">Pending changes, oldest first. You can't approve your own requests.</p>
      <ErrorBox error={q.error} />
      <RequestTable rows={q.data} empty="Nothing to review." />
    </div>
  )
}
