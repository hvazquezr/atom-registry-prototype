import type { AnswerCheck, Field, FormDefinition, ResolvedItem, Values } from '../api'
import { showValue } from '../hooks'
import FieldInput from './FieldInput'

function asField(item: ResolvedItem, required: boolean): Field {
  return {
    key: item.key, label: item.label, description: item.help_text,
    type: item.input.type === 'boolean' ? 'options' : item.input.type,
    required, config: item.input.config, calculated: false, expression: null, depends_on: [], time_dependent: false, position: 0,
  }
}

function BooleanInput({ item, value, required, error, disabled, onChange }: {
  item: ResolvedItem; value: unknown; required: boolean; error?: string; disabled?: boolean; onChange: (v: unknown) => void
}) {
  return (
    <div className={`form-field ${error ? 'has-error' : ''}`}>
      <label>{item.label}{required ? <span className="req"> *</span> : <span className="opt">optional</span>}</label>
      {item.help_text && <div className="muted small">{item.help_text}</div>}
      <div className="row">
        {[true, false].map((v) => (
          <label key={String(v)} className="check">
            <input type="radio" name={item.key} checked={value === v} disabled={disabled} onChange={() => onChange(v)} /> {v ? 'Yes' : 'No'}
          </label>
        ))}
        {!disabled && value !== undefined && value !== null && <button className="link small" onClick={() => onChange(null)}>Clear</button>}
      </div>
      {error && <div className="field-error">{error}</div>}
    </div>
  )
}

/** Renders a resolved form's questions: hides what Required if hides, shows calculated answers read-only. */
export default function FormFields({ definition, answers, check, errors, readOnly, onChange }: {
  definition: FormDefinition
  answers: Values
  check?: AnswerCheck
  errors: Record<string, string>
  readOnly?: boolean
  onChange: (key: string, v: unknown) => void
}) {
  const state = new Map(check?.fields.map((f) => [f.key, f]))
  return (
    <>
      {definition.sections.map((s, si) => (
        <fieldset key={si} className="preview-section">
          {s.title && <legend>{s.title}</legend>}
          {s.description && <div className="muted small">{s.description}</div>}
          {s.items.map((it) => {
            const st = state.get(it.key)
            const visible = st ? st.visible : it.conditions.length === 0
            if (!visible) return null
            const required = st ? st.required : it.required
            if (it.calculation) {
              const v = st ? st.value : answers[it.key]
              return (
                <div key={it.key} className={`preview-item calculated ${it.parent ? 'child' : ''}`}>
                  <div className={`form-field ${errors[it.key] ? 'has-error' : ''}`}>
                    <label>{it.label} <span className="pill blue">calculated</span></label>
                    {it.help_text && <div className="muted small">{it.help_text}</div>}
                    <div className="calc-value">
                      {v === undefined || v === null ? <span className="muted">Filled in once {it.calculation.inputs.map((i) => i.atom_name ?? i.key).join(' and ')} {it.calculation.inputs.length > 1 ? 'are' : 'is'} answered</span> : showValue(v)}
                    </div>
                    {errors[it.key] && <div className="field-error">{errors[it.key]}</div>}
                  </div>
                </div>
              )
            }
            if (readOnly) {
              const v = showValue(answers[it.key])
              return (
                <div key={it.key} className={`preview-item ${it.parent ? 'child' : ''}`}>
                  <div className="form-field">
                    <label>{it.label}{required ? <span className="req"> *</span> : <span className="opt">optional</span>}</label>
                    <div className="calc-value">{v || <span className="muted">Not answered</span>}</div>
                  </div>
                </div>
              )
            }
            return (
              <div key={it.key} className={`preview-item ${it.parent ? 'child' : ''} ${it.conditions.length ? 'conditional' : ''}`}>
                {it.input.type === 'boolean' ? (
                  <BooleanInput item={it} value={answers[it.key]} required={required} error={errors[it.key]} onChange={(v) => onChange(it.key, v)} />
                ) : (
                  <FieldInput field={asField(it, required)} value={answers[it.key]} error={errors[it.key]} markOptional onChange={(v) => onChange(it.key, v)} />
                )}
              </div>
            )
          })}
        </fieldset>
      ))}
    </>
  )
}
