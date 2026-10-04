export class ApiError extends Error {
  constructor(status, errors) {
    super(Object.values(errors).flat()[0] || `Request failed (${status})`)
    this.status = status
    this.errors = errors
  }
}

function getCookie(name) {
  const match = document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.split('=')[1]) : null
}

async function ensureCsrfCookie() {
  if (!getCookie('csrftoken')) {
    await fetch('/api/auth/csrf/', { credentials: 'same-origin' })
  }
  return getCookie('csrftoken')
}

async function request(method, path, body) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRFToken'] = await ensureCsrfCookie()

  let response
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, { non_field_errors: ['Няма връзка със сървъра.'] })
  }

  if (response.status === 204) return null

  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new ApiError(response.status, data.errors || { non_field_errors: [`Грешка ${response.status}`] })
  }
  return data
}

export const authApi = {
  csrf: ensureCsrfCookie,
  me: () => request('GET', '/auth/me/'),
  register: (payload) => request('POST', '/auth/register/', payload),
  login: (payload) => request('POST', '/auth/login/', payload),
  logout: () => request('POST', '/auth/logout/'),
  updateProfile: (payload) => request('PATCH', '/auth/me/', payload),
}
