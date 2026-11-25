import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import ErrorBox from '../components/ErrorBox'
import { formatDate } from '../hooks'

interface Token {
  id: string
  name: string
  prefix: string
  scopes: string[]
  username: string
  created_at: string
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
}

export default function Tokens() {
  const qc = useQueryClient()
  const { me } = useAuth()
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>([])
  const [created, setCreated] = useState<string | null>(null)
  const meta = useQuery({ queryKey: ['scopes'], queryFn: () => api<{ scopes: Record<string, string> }>('/internal/meta/scopes') })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api<Token[]>('/internal/tokens') })
  const create = useMutation({
    mutationFn: () => api<Token & { token: string }>('/internal/tokens', { body: { name, scopes: scopes.length ? scopes : undefined } }),
    onSuccess: (t) => {
      setCreated(t.token)
      setName('')
      setScopes([])
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const revoke = useMutation({ mutationFn: (id: string) => api(`/internal/tokens/${id}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['tokens'] }) })
  const mine = Object.entries(meta.data?.scopes ?? {}).filter(([s]) => me?.scopes.includes(s))

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>API tokens</h1>
          <div className="muted small">For scripts and other systems. Send as <code>Authorization: Bearer &lt;token&gt;</code>.</div>
        </div>
      </div>
      <div className="card">
        <h3>Create a token</h3>
        <div className="form-field"><label>Name</label><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Forms service" /></div>
        <div className="form-field">
          <label>Scopes (leave empty for everything you're allowed)</label>
          <div className="checks">
            {mine.map(([s, d]) => (
              <label key={s} className="check" title={d}>
                <input type="checkbox" checked={scopes.includes(s)} onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
                <span className="mono">{s}</span> <span className="muted small">{d}</span>
              </label>
            ))}
          </div>
        </div>
        <ErrorBox error={create.error} />
        <button className="primary" disabled={!name || create.isPending} onClick={() => create.mutate()}>Create token</button>
        {created && (
          <div className="notice">
            Copy this token now. It won't be shown again.
            <pre className="token">{created}</pre>
          </div>
        )}
      </div>
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Name</th><th>Scopes</th><th>Created</th><th>Last used</th><th /></tr></thead>
          <tbody>
            {tokens.data?.map((t) => (
              <tr key={t.id} className={t.revoked_at ? 'dim' : ''}>
                <td>{t.name}<div className="mono small muted">{t.prefix}… · {t.username}</div></td>
                <td className="small mono">{t.scopes.join(' ')}</td>
                <td className="small">{formatDate(t.created_at)}</td>
                <td className="small">{t.last_used_at ? formatDate(t.last_used_at) : 'never'}</td>
                <td className="right">{t.revoked_at ? <span className="pill gray">revoked</span> : <button className="link danger" onClick={() => revoke.mutate(t.id)}>Revoke</button>}</td>
              </tr>
            ))}
            {tokens.data?.length === 0 && <tr><td colSpan={5} className="muted center">No tokens yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
