import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, qs, type ChangeRequest } from '../api'
import ErrorBox from '../components/ErrorBox'
import { RequestTable } from './ReviewQueue'

export default function MyRequests() {
  const [status, setStatus] = useState('all')
  const q = useQuery({
    queryKey: ['my-requests', status],
    queryFn: () => api<ChangeRequest[]>(`/api/v1/change-requests${qs({ mine: 'true', status })}`),
  })
  return (
    <div>
      <div className="page-head">
        <h1>My change requests</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {['all', 'pending', 'approved', 'rejected', 'withdrawn'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <ErrorBox error={q.error} />
      <RequestTable rows={q.data} empty="You haven't submitted any changes." />
    </div>
  )
}
