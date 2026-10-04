import { useState } from 'react'
import { useAuth } from '../auth/useAuth.js'
import { FormErrors, TextField } from '../components/Form.jsx'

const FIELDS = ['username', 'password']

export default function LoginPage() {
  const { login } = useAuth()
  const [form, setForm] = useState({ username: '', password: '' })
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)

  const onChange = (event) => setForm({ ...form, [event.target.name]: event.target.value })

  async function onSubmit(event) {
    event.preventDefault()
    setSubmitting(true)
    setErrors({})
    try {
      await login(form)
    } catch (error) {
      setErrors(error.errors || { non_field_errors: [error.message] })
      setForm((current) => ({ ...current, password: '' }))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="card">
      <h1>Вход</h1>
      <form onSubmit={onSubmit} noValidate>
        <FormErrors errors={errors} fields={FIELDS} />
        <TextField label="Потребителско име" name="username" value={form.username} onChange={onChange} errors={errors} autoComplete="username" required />
        <TextField label="Парола" name="password" type="password" value={form.password} onChange={onChange} errors={errors} autoComplete="current-password" required />
        <button type="submit" disabled={submitting}>
          {submitting ? 'Влизане…' : 'Вход'}
        </button>
      </form>
      <p className="card__footer">
        Нямаш акаунт? <a href="#/register">Регистрирай се</a>
      </p>
    </section>
  )
}
