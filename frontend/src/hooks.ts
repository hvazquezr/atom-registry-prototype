import { useQuery } from '@tanstack/react-query'
import { api, type Field, type RelationType, type Schema } from './api'

export function useSchema() {
  return useQuery({ queryKey: ['schema'], queryFn: () => api<Schema>('/api/v1/schema') })
}

export function useRelationTypes() {
  return useQuery({ queryKey: ['relation-types'], queryFn: () => api<RelationType[]>('/api/v1/relation-types') })
}

export function editableFields(fields: Field[] | undefined): Field[] {
  return (fields ?? []).filter((f) => !f.calculated)
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function showValue(v: unknown): string {
  if (v === undefined || v === null || v === '') return ''
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}
