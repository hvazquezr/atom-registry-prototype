import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type RelationType } from '../api'
import { useAuth } from '../auth'
import ErrorBox from '../components/ErrorBox'
import { useRelationTypes } from '../hooks'

const CARDINALITY: Record<string, string> = {
  many_to_many: 'Many to many',
  one_to_many: 'One to many (a target has one source)',
  many_to_one: 'Many to one (a source has one target)',
  one_to_one: 'One to one',
}

const empty = { key: '', label: '', inverse_label: '', description: '', directed: true, cardinality: 'many_to_many', allow_cycles: true, require_note: true }

function Editor({ rt, onClose }: { rt: RelationType | null; onClose: () => void }) {
  const [v, setV] = useState<typeof empty>(rt ? { ...rt } : empty)
  const save = useMutation({
    mutationFn: () =>
      rt
        ? api(`/internal/relation-types/${rt.id}`, { method: 'PATCH', body: { label: v.label, inverse_label: v.inverse_label, description: v.description, directed: v.directed, cardinality: v.cardinality, allow_cycles: v.allow_cycles, require_note: v.require_note } })
        : api('/internal/relation-types', { body: v }),
    onSuccess: onClose,
  })
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="row spread"><h2>{rt ? rt.label : 'New relation type'}</h2><button className="link" onClick={onClose}>Close</button></div>
        <div className="grid2">
          <div className="form-field"><label>Label</label><input value={v.label} onChange={(e) => setV({ ...v, label: e.target.value })} placeholder="Depends on" /></div>
          <div className="form-field"><label>Key</label><input className="mono" value={v.key} disabled={!!rt} onChange={(e) => setV({ ...v, key: e.target.value })} placeholder="dependency" /></div>
        </div>
        <div className="form-field"><label>Label from the other side</label><input value={v.inverse_label} onChange={(e) => setV({ ...v, inverse_label: e.target.value })} placeholder="Dependency of" /></div>
        <div className="form-field"><label>Description</label><textarea rows={2} value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} /></div>
        <div className="form-field">
          <label>Cardinality</label>
          <select value={v.cardinality} onChange={(e) => setV({ ...v, cardinality: e.target.value })}>
            {Object.entries(CARDINALITY).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
        <label className="check"><input type="checkbox" checked={v.directed} onChange={(e) => setV({ ...v, directed: e.target.checked })} /> Has a direction (A → B is different from B → A)</label>
        <label className="check"><input type="checkbox" checked={!v.allow_cycles} onChange={(e) => setV({ ...v, allow_cycles: !e.target.checked })} /> Refuse loops (A → B → A)</label>
        <label className="check"><input type="checkbox" checked={v.require_note} onChange={(e) => setV({ ...v, require_note: e.target.checked })} /> Every link needs a note</label>
        <ErrorBox error={save.error} />
        <div className="actions"><button className="primary" onClick={() => save.mutate()}>Save</button></div>
      </div>
    </div>
  )
}

export default function RelationTypes() {
  const qc = useQueryClient()
  const { can } = useAuth()
  const types = useRelationTypes()
  const [editing, setEditing] = useState<RelationType | null | undefined>(undefined)
  const toggle = useMutation({
    mutationFn: (rt: RelationType) => api(`/internal/relation-types/${rt.id}`, { method: 'PATCH', body: { status: rt.status === 'active' ? 'deprecated' : 'active' } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['relation-types'] }),
  })
  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Relation types</h1>
          <div className="muted small">The ways one atom can be linked to another. Changes apply right away.</div>
        </div>
        {can('schema:admin') && <button className="primary" onClick={() => setEditing(null)}>New relation type</button>}
      </div>
      <ErrorBox error={types.error ?? toggle.error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Relation</th><th>Rules</th><th>Status</th><th /></tr></thead>
          <tbody>
            {types.data?.map((rt) => (
              <tr key={rt.id} className={rt.status === 'deprecated' ? 'dim' : ''}>
                <td>
                  <button className="link strong" onClick={() => can('schema:admin') && setEditing(rt)}>{rt.label}</button>
                  {rt.inverse_label && <span className="muted"> / {rt.inverse_label}</span>}
                  <div className="mono small muted">{rt.key}</div>
                  {rt.description && <div className="small muted">{rt.description}</div>}
                </td>
                <td className="small">
                  {CARDINALITY[rt.cardinality]}
                  <div className="muted">{rt.directed ? 'Directed' : 'Undirected'} · {rt.allow_cycles ? 'loops allowed' : 'no loops'} · {rt.require_note ? 'note required' : 'note optional'}</div>
                </td>
                <td><span className={`pill ${rt.status === 'active' ? 'approved' : 'gray'}`}>{rt.status}</span></td>
                <td className="right">{can('schema:admin') && <button className="link" onClick={() => toggle.mutate(rt)}>{rt.status === 'active' ? 'Deprecate' : 'Restore'}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing !== undefined && <Editor rt={editing} onClose={() => { setEditing(undefined); qc.invalidateQueries({ queryKey: ['relation-types'] }) }} />}
    </div>
  )
}
