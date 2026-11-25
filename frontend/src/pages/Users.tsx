import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ROLE_LABELS } from '../api'
import ErrorBox from '../components/ErrorBox'

interface User {
  id: string
  username: string
  display_name: string
  roles: string[]
  is_service_account: boolean
  is_active: boolean
}
const ROLES = ['viewer', 'atom_editor', 'reviewer', 'record_manager', 'admin']

export default function Users() {
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/internal/users') })
  const [form, setForm] = useState({ username: '', display_name: '', password: '', roles: ['atom_editor'], is_service_account: false })
  const create = useMutation({
    mutationFn: () => api('/internal/users', { body: { ...form, password: form.password || undefined } }),
    onSuccess: () => {
      setForm({ username: '', display_name: '', password: '', roles: ['atom_editor'], is_service_account: false })
      qc.invalidateQueries({ queryKey: ['users'] })
    },
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<User> }) => api(`/internal/users/${id}`, { method: 'PATCH', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })
  const toggleRole = (roles: string[], r: string) => (roles.includes(r) ? roles.filter((x) => x !== r) : [...roles, r])

  return (
    <div>
      <div className="page-head"><h1>Users</h1></div>
      <p className="muted small">Viewers read atoms, forms and records. Atom editors propose atom changes and build forms. Reviewers approve other people's changes. Record managers create and edit records. Admins do everything, including fields, relation types and users.</p>
      <ErrorBox error={users.error ?? update.error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>User</th><th>Roles</th><th>Status</th></tr></thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id} className={u.is_active ? '' : 'dim'}>
                <td>{u.display_name || u.username}<div className="mono small muted">{u.username}{u.is_service_account && ' · service account'}</div></td>
                <td>
                  <div className="checks inline">
                    {ROLES.map((r) => (
                      <label key={r} className="check">
                        <input type="checkbox" checked={u.roles.includes(r)} onChange={() => update.mutate({ id: u.id, body: { roles: toggleRole(u.roles, r) } })} /> {ROLE_LABELS[r] ?? r}
                      </label>
                    ))}
                  </div>
                </td>
                <td><button className="link" onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? 'Disable' : 'Enable'}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h3>Add a user</h3>
        <div className="grid2">
          <div className="form-field"><label>Username</label><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></div>
          <div className="form-field"><label>Display name</label><input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></div>
        </div>
        <label className="check"><input type="checkbox" checked={form.is_service_account} onChange={(e) => setForm({ ...form, is_service_account: e.target.checked })} /> Service account (API tokens only, no password)</label>
        {!form.is_service_account && (
          <div className="form-field"><label>Password</label><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
        )}
        <div className="checks inline">
          {ROLES.map((r) => (
            <label key={r} className="check"><input type="checkbox" checked={form.roles.includes(r)} onChange={() => setForm({ ...form, roles: toggleRole(form.roles, r) })} /> {ROLE_LABELS[r] ?? r}</label>
          ))}
        </div>
        <ErrorBox error={create.error} />
        <button className="primary" disabled={!form.username || create.isPending} onClick={() => create.mutate()}>Add user</button>
      </div>
    </div>
  )
}
