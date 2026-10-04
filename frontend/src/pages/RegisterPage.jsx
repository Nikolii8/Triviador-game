import { useState } from 'react'
import { useAuth } from '../auth/useAuth.js'
import { FormErrors, TextField } from '../components/Form.jsx'

const FIELDS = ['username', 'email', 'nickname', 'password', 'password_confirm']
const EMPTY_FORM = { username: '', email: '', nickname: '', password: '', password_confirm: '' }

export default function RegisterPage() {
  const { register } = useAuth()
  const [form, setForm] = useState(EMPTY_FORM)
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)

  const onChange = (event) => setForm({ ...form, [event.target.name]: event.target.value })

  async function onSubmit(event) {
    event.preventDefault()
    setSubmitting(true)
    setErrors({})
    try {
      await register(form)
    } catch (error) {
      setErrors(error.errors || { non_field_errors: [error.message] })
    } finally {
      setSubmitting(false)
    }
  }

  const field = (name, label, props = {}) => (
    <TextField label={label} name={name} value={form[name]} onChange={onChange} errors={errors} required {...props} />
  )

  return (
    <section className="card">
      <h1>Регистрация</h1>
      <form onSubmit={onSubmit} noValidate>
        <FormErrors errors={errors} fields={FIELDS} />
        {field('username', 'Потребителско име', { autoComplete: 'username' })}
        {field('email', 'Имейл', { type: 'email', autoComplete: 'email' })}
        {field('nickname', 'Nickname в играта', { maxLength: 30 })}
        {field('password', 'Парола', { type: 'password', autoComplete: 'new-password' })}
        {field('password_confirm', 'Повтори паролата', { type: 'password', autoComplete: 'new-password' })}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Регистриране…' : 'Регистрация'}
        </button>
      </form>
      <p className="card__footer">
        Вече имаш акаунт? <a href="#/login">Вход</a>
      </p>
    </section>
  )
}
