import { useEffect } from 'react'
import { useAuth } from './auth/useAuth.js'
import { Avatar } from './components/Avatar.jsx'
import { Logo } from './components/Logo.jsx'
import LoginPage from './pages/LoginPage.jsx'
import ProfilePage from './pages/ProfilePage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import { navigate, useRoute } from './router.js'

const PUBLIC_ROUTES = { '/login': LoginPage, '/register': RegisterPage }
const PRIVATE_ROUTES = { '/profile': ProfilePage }

export default function App() {
  const { user, loading, logout } = useAuth()
  const route = useRoute()

  // Guests may only see login/register; logged-in users are sent to their profile.
  const redirect = loading ? null : user ? (PRIVATE_ROUTES[route] ? null : '/profile') : PUBLIC_ROUTES[route] ? null : '/login'

  useEffect(() => {
    if (redirect) navigate(redirect)
  }, [redirect])

  const Page = !loading && !redirect ? (user ? PRIVATE_ROUTES[route] : PUBLIC_ROUTES[route]) : null

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/" aria-label="Triviador">
          <Logo small />
        </a>
        {user && (
          <nav className="topbar__user">
            <Avatar avatarKey={user.profile.avatar_key} size={32} />
            <span className="topbar__nick">{user.profile.nickname}</span>
            <button type="button" className="btn btn--yellow" onClick={logout}>
              Log out
            </button>
          </nav>
        )}
      </header>
      <main className="content">{Page ? <Page key={route} /> : <p className="loading">Loading…</p>}</main>
    </div>
  )
}
