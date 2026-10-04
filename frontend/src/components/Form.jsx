export function FieldErrors({ messages }) {
  if (!messages?.length) return null
  return (
    <ul className="field-errors" role="alert">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  )
}

/** Errors that do not belong to a visible field (non_field_errors, detail, ...). */
export function FormErrors({ errors, fields }) {
  const messages = Object.entries(errors || {})
    .filter(([key]) => !fields.includes(key))
    .flatMap(([, value]) => value)
  if (!messages.length) return null
  return (
    <div className="form-errors" role="alert">
      {messages.map((message) => (
        <p key={message}>{message}</p>
      ))}
    </div>
  )
}

export function TextField({ label, name, errors, ...inputProps }) {
  const fieldErrors = errors?.[name]
  return (
    <label className={`field${fieldErrors ? ' field--invalid' : ''}`}>
      <span className="field__label">{label}</span>
      <input name={name} aria-invalid={Boolean(fieldErrors)} {...inputProps} />
      <FieldErrors messages={fieldErrors} />
    </label>
  )
}
