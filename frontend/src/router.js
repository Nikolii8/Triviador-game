import { useEffect, useState } from 'react'

function currentRoute() {
  return window.location.hash.replace(/^#/, '') || '/'
}

export function navigate(path) {
  window.location.hash = path
}

export function useRoute() {
  const [route, setRoute] = useState(currentRoute)

  useEffect(() => {
    const onChange = () => setRoute(currentRoute())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return route
}
