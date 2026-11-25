import { ApiError } from '../api'

export default function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  if (error instanceof ApiError) {
    const details = error.fieldErrors
    return (
      <div className="error-box">
        <strong>{error.message}</strong>
        {details.length > 0 && (
          <ul>
            {details.map((d, i) => (
              <li key={i}>{d.message}</li>
            ))}
          </ul>
        )}
      </div>
    )
  }
  return <div className="error-box">{String((error as Error)?.message ?? error)}</div>
}

export function fieldErrorMap(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const out: Record<string, string> = {}
  for (const e of error.fieldErrors) out[e.field] = out[e.field] ? `${out[e.field]} ${e.message}` : e.message
  return out
}
