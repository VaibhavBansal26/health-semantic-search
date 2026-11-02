// All backend calls live here. Relative URLs only (same origin).
let token = null
let onUnauthorized = () => {}

const KEY = 'hss_token'

export function setToken(t) {
  token = t
  try {
    if (t) sessionStorage.setItem(KEY, t)
    else sessionStorage.removeItem(KEY)
  } catch { /* storage unavailable */ }
}

export function loadStoredToken() {
  try {
    token = sessionStorage.getItem(KEY)
  } catch {
    token = null
  }
  return token
}

export const hasToken = () => !!token
export function setUnauthorizedHandler(fn) { onUnauthorized = fn }

export class ApiError extends Error {
  constructor(status, message, retryAfter) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function detailText(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  return ''
}

async function request(method, path, body, { auth = true, retried = false } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && token) headers.Authorization = `Bearer ${token}`
  let res
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.')
  }
  let data = null
  try { data = await res.json() } catch { /* empty or non-JSON body */ }
  if (res.ok) return data

  if (res.status === 503 && !retried) {
    await sleep(1000)
    return request(method, path, body, { auth, retried: true })
  }
  const detail = detailText(data && data.detail)
  if (res.status === 401 && auth) onUnauthorized()
  const fallback = {
    401: 'Your session is invalid or has expired. Please sign in again.',
    403: 'You do not have permission to do that.',
    422: 'The request was not valid. Please check your input.',
    429: 'Too many attempts. Please wait a bit and try again.',
    503: 'The search index is updating. Please try again in a moment.',
  }[res.status] || `Request failed (${res.status}).`
  throw new ApiError(res.status, detail || fallback, res.headers.get('Retry-After'))
}

export const getConfig = () => request('GET', '/config', undefined, { auth: false })
export const register = (email, password) => request('POST', '/auth/register', { email, password }, { auth: false })
export const login = (email, password) => request('POST', '/auth/login', { email, password }, { auth: false })
export const demoLogin = (persona) => request('POST', '/auth/demo', { persona }, { auth: false })
export const me = () => request('GET', '/auth/me')
export const logout = () => request('POST', '/auth/logout')
export const search = (query, mode, k = 5) => request('POST', '/search', { query, mode, k })
export const answer = (query) => request('POST', '/answer', { query })
export const adminNotFound = () => request('GET', '/admin/not-found')
export const adminRevoke = (payload) => request('POST', '/admin/revoke', payload)
