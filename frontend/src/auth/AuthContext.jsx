import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, authApi } from '../api.js'
import { AuthContext } from './context.js'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // Restore the session after a page refresh: the session cookie is sent automatically.
  useEffect(() => {
    let cancelled = false
    authApi
      .csrf()
      .then(() => authApi.me())
      .then((me) => !cancelled && setUser(me))
      .catch((error) => {
        if (!(error instanceof ApiError) || ![401, 403].includes(error.status)) console.error(error)
        if (!cancelled) setUser(null)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (credentials) => setUser(await authApi.login(credentials)), [])
  const register = useCallback(async (payload) => {
    await authApi.register(payload)
    // Registration does not start a session, so log in right away.
    setUser(await authApi.login({ username: payload.username, password: payload.password }))
  }, [])
  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      setUser(null)
    }
  }, [])
  const updateProfile = useCallback(async (changes) => setUser(await authApi.updateProfile(changes)), [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout, updateProfile }),
    [user, loading, login, register, logout, updateProfile],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
