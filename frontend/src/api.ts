export type FieldType = 'text' | 'number' | 'date' | 'list' | 'options'

export interface Choice {
  value: string
  label?: string
}

export interface FieldConfig {
  min_length?: number
  max_length?: number
  regex?: string
  regex_message?: string
  placeholder?: string
  min?: number | string
  max?: number | string
  integer_only?: boolean
  unit?: string
  choices?: Choice[]
  multiple?: boolean
  min_selected?: number
  max_selected?: number
  item_type?: 'text' | 'number' | 'date'
  item?: FieldConfig
  min_items?: number
  max_items?: number
  unique_items?: boolean
}

export interface Field {
  id?: string
  key: string
  label: string
  description: string
  type: FieldType
  required: boolean
  config: FieldConfig
  calculated: boolean
  expression: string | null
  depends_on: string[]
  time_dependent: boolean
  position: number
  status?: 'active' | 'deprecated'
  system?: boolean
  published?: boolean
}

export type Values = Record<string, unknown>

export interface Atom {
  id: string
  schema_version: number
  status: 'active' | 'deprecated'
  revision: number
  values: Values
  created_at: string
  updated_at: string
  pending_change_requests?: number
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface RelationType {
  id: string
  key: string
  label: string
  inverse_label: string
  description: string
  directed: boolean
  cardinality: 'one_to_one' | 'one_to_many' | 'many_to_one' | 'many_to_many'
  allow_cycles: boolean
  require_note: boolean
  attributes: unknown[]
  status: 'active' | 'deprecated'
}

export interface Relation {
  id: string
  type: string
  type_label: string
  label: string
  direction: 'out' | 'in'
  note: string
  other_atom_id: string
  other_atom_name: string | null
  source_atom_id: string
  target_atom_id: string
}

export interface RelationAdd {
  type: string
  target_atom_id: string
  direction: 'out' | 'in'
  note: string
}

export interface ChangeRequest {
  id: string
  atom_id: string | null
  atom_name: string | null
  atom_status: string | null
  kind: 'create' | 'update' | 'deprecate' | 'restore'
  proposed_values: Values
  proposed_relations: { add?: RelationAdd[]; remove?: string[] }
  base_revision: number | null
  comment: string
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn'
  submitted_by: string
  submitted_by_name: string | null
  submitted_at: string
  reviewed_by_name: string | null
  reviewed_at: string | null
  review_comment: string
  stale: boolean
  can_review?: boolean
  current_values?: Values | null
  current_revision?: number | null
  relation_targets?: Record<string, string>
  relations_removed?: Relation[]
}

export interface Revision {
  revision: number
  schema_version: number
  values: Values
  status: string
  relations: Relation[]
  summary: string
  changed_by_name: string | null
  changed_at: string
}

export interface Schema {
  version: number
  published_at: string
  fields: Field[]
  relation_types: RelationType[]
}

export interface Me {
  id: string
  username: string
  display_name: string
  roles: string[]
  scopes: string[]
}

export interface FieldError {
  field: string
  code: string
  message: string
}

export class ApiError extends Error {
  status: number
  code: string
  details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }

  get fieldErrors(): FieldError[] {
    return Array.isArray(this.details) ? (this.details as FieldError[]) : []
  }
}

const TOKEN_KEY = 'atom-registry-token'

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers ?? {}) }
  const token = tokenStore.get()
  if (token) headers.Authorization = `Bearer ${token}`
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, {
    method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401 && !path.endsWith('/auth/login')) onUnauthorized()
    const err = data?.error ?? {}
    throw new ApiError(res.status, err.code ?? 'error', err.message ?? res.statusText, err.details)
  }
  return data as T
}

export function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.append(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export function atomName(values: Values | null | undefined, fallback = 'Untitled'): string {
  const n = values?.name
  return typeof n === 'string' && n ? n : fallback
}
