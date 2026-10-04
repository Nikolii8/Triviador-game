import { useState } from 'react'
import { useAuth } from '../auth/useAuth.js'
import { AVATAR_KEYS } from '../avatars.js'
import { Avatar } from '../components/Avatar.jsx'
import { FieldErrors, FormErrors, TextField } from '../components/Form.jsx'

const FIELDS = ['nickname', 'avatar_key']

export default function ProfilePage() {
  const { user, updateProfile } = useAuth()
  const [form, setForm] = useState({ nickname: user.profile.nickname, avatar_key: user.profile.avatar_key })
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const isDirty = form.nickname !== user.profile.nickname || form.avatar_key !== user.profile.avatar_key

  function update(name, value) {
    setForm({ ...form, [name]: value })
    setSaved(false)
  }

  async function onSubmit(event) {
    event.preventDefault()
    setSaving(true)
    setErrors({})
    try {
      await updateProfile(form)
      setSaved(true)
    } catch (error) {
      setErrors(error.errors || { non_field_errors: [error.message] })
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="panel panel--wide">
      <h1 className="plaque">Профил</h1>
      <header className="profile-header">
        <Avatar avatarKey={user.profile.avatar_key} size={80} />
        <div>
          <h2 className="profile-header__nick">{user.profile.nickname}</h2>
          <p className="muted">
            @{user.username} · {user.email}
          </p>
        </div>
      </header>

      <form onSubmit={onSubmit} noValidate>
        <FormErrors errors={errors} fields={FIELDS} />
        <TextField label="Nickname" name="nickname" value={form.nickname} onChange={(e) => update('nickname', e.target.value)} errors={errors} maxLength={30} required />

        <fieldset className="avatar-picker">
          <legend className="field__label">Аватар</legend>
          <div className="avatar-picker__options">
            {AVATAR_KEYS.map((key) => (
              <label key={key} className={`avatar-option${form.avatar_key === key ? ' avatar-option--selected' : ''}`}>
                <input type="radio" name="avatar_key" value={key} checked={form.avatar_key === key} onChange={() => update('avatar_key', key)} />
                <Avatar avatarKey={key} size={56} />
                <span>{key}</span>
              </label>
            ))}
          </div>
          <FieldErrors messages={errors.avatar_key} />
        </fieldset>

        <button type="submit" className="btn btn--red" disabled={saving || !isDirty}>
          {saving ? 'Запазване…' : 'Запази промените'}
        </button>
        {saved && <p className="success" role="status">Профилът е обновен.</p>}
      </form>
    </section>
  )
}
