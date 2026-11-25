import { useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api, type ChangeRequest } from './api'
import { useAuth } from './auth'
import AtomDetail from './pages/AtomDetail'
import AtomEdit from './pages/AtomEdit'
import AtomList from './pages/AtomList'
import ChangeRequestPage from './pages/ChangeRequestPage'
import Login from './pages/Login'
import MyRequests from './pages/MyRequests'
import RelationTypes from './pages/RelationTypes'
import ReviewQueue from './pages/ReviewQueue'
import SchemaAdmin from './pages/SchemaAdmin'
import Tokens from './pages/Tokens'
import Users from './pages/Users'

function ReviewBadge() {
  const { data } = useQuery({
    queryKey: ['review-queue'],
    queryFn: () => api<ChangeRequest[]>('/internal/review-queue'),
    refetchInterval: 30_000,
  })
  const n = data?.filter((c) => c.can_review).length ?? 0
  return n ? <span className="badge">{n}</span> : null
}

export default function App() {
  const { me, loading, logout, can } = useAuth()
  if (loading) return <div className="center muted">Loading…</div>
  if (!me) return <Login />

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">◆</span> Atom Registry
        </div>
        <nav>
          <div className="nav-group">Registry</div>
          <NavLink to="/atoms" end>Atoms</NavLink>
          {can('atoms:write') && <NavLink to="/atoms/new">New atom</NavLink>}
          {can('atoms:write') && <NavLink to="/requests">My requests</NavLink>}
          {can('atoms:approve') && (
            <NavLink to="/review">
              Review queue <ReviewBadge />
            </NavLink>
          )}
          <div className="nav-group">Meta atom</div>
          <NavLink to="/schema">Fields</NavLink>
          <NavLink to="/relation-types">Relation types</NavLink>
          <div className="nav-group">Access</div>
          <NavLink to="/tokens">API tokens</NavLink>
          {can('users:admin') && <NavLink to="/users">Users</NavLink>}
          <a href="/docs" target="_blank" rel="noreferrer" className="external">API docs ↗</a>
        </nav>
        <div className="me">
          <div>{me.display_name}</div>
          <div className="muted small">{me.roles.join(', ')}</div>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/atoms" replace />} />
          <Route path="/atoms" element={<AtomList />} />
          <Route path="/atoms/new" element={<AtomEdit />} />
          <Route path="/atoms/:id" element={<AtomDetail />} />
          <Route path="/atoms/:id/edit" element={<AtomEdit />} />
          <Route path="/requests" element={<MyRequests />} />
          <Route path="/requests/:id" element={<ChangeRequestPage />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/schema" element={<SchemaAdmin />} />
          <Route path="/relation-types" element={<RelationTypes />} />
          <Route path="/tokens" element={<Tokens />} />
          <Route path="/users" element={<Users />} />
          <Route path="*" element={<div className="muted">Page not found.</div>} />
        </Routes>
      </main>
    </div>
  )
}
