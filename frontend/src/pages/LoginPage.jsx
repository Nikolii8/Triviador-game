import { useState } from 'react'
import { useAuth } from '../auth/useAuth.js'
import { FormErrors, TextField } from '../components/Form.jsx'
import { Logo } from '../components/Logo.jsx'

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
    <section className="panel">
      <Logo />
      <h1 className="plaque">Вход</h1>
      <form onSubmit={onSubmit} noValidate>
        <FormErrors errors={errors} fields={FIELDS} />
        <TextField label="Потребителско име" name="username" value={form.username} onChange={onChange} errors={errors} autoComplete="username" required />
        <TextField label="Парола" name="password" type="password" value={form.password} onChange={onChange} errors={errors} autoComplete="current-password" required />
        <button type="submit" className="btn btn--red" disabled={submitting}>
          {submitting ? 'Влизане…' : 'Влез в играта'}
        </button>
      </form>
      <p className="panel__hint">Нямаш акаунт?</p>
      <a className="btn btn--wood" href="#/register">
        Създай акаунт
      </a>
    </section>
  )
}
