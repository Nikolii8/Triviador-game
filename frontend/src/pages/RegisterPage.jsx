import { useState } from 'react'
import { useAuth } from '../auth/useAuth.js'
import { FormErrors, TextField } from '../components/Form.jsx'
import { Logo } from '../components/Logo.jsx'

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
    <section className="panel">
      <Logo />
      <h1 className="plaque">Sign up</h1>
      <form onSubmit={onSubmit} noValidate>
        <FormErrors errors={errors} fields={FIELDS} />
        {field('username', 'Username', { autoComplete: 'username' })}
        {field('email', 'Email', { type: 'email', autoComplete: 'email' })}
        {field('nickname', 'In-game nickname', { maxLength: 30 })}
        {field('password', 'Password', { type: 'password', autoComplete: 'new-password' })}
        {field('password_confirm', 'Confirm password', { type: 'password', autoComplete: 'new-password' })}
        <button type="submit" className="btn btn--red" disabled={submitting}>
          {submitting ? 'Creating account…' : 'Sign up'}
        </button>
      </form>
      <p className="panel__hint">Already have an account?</p>
      <a className="btn btn--wood" href="#/login">
        Log in
      </a>
    </section>
  )
}
