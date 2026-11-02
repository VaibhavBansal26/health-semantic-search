// Tiny mock of the backend contract. Run: node dev/mock-server.mjs  (port 8000)
import http from 'node:http'

const PORT = Number(process.env.PORT || 8000)
const personas = [
  { id: 'anon', label: 'Public only', description: 'No access groups; public labels only.' },
  { id: 'cardio', label: 'Cardiology team', description: 'Synthetic group: cardiology.' },
  { id: 'admin', label: 'Admin', description: 'Can view not-found queries and revoke tokens.' },
]
const users = {
  anon: { email: 'anon@demo.local', groups: [], roles: [], tenant: null },
  cardio: { email: 'cardio@demo.local', groups: ['cardiology'], roles: [], tenant: 'acme' },
  admin: { email: 'admin@demo.local', groups: ['cardiology', 'oncology'], roles: ['admin'], tenant: 'acme' },
}
const config = {
  engine: 'full', modes: ['keyword', 'dense', 'hybrid', 'rerank'], answers: true,
  demo_personas: personas, registration: true,
  notice: 'Demo build: mock backend with canned data. Access groups are synthetic.',
}
const docs = [
  { label: '5d7b8a8e-0000-4a11-9f3c-0123456789ab', drug: 'Ibuprofen', section: 'Pregnancy', text: 'Use of NSAIDs, including ibuprofen, during the third trimester of pregnancy increases the risk of premature closure of the fetal ductus arteriosus. Avoid use in pregnant women starting at 30 weeks of gestation. Between about 20 and 30 weeks, limit use to the lowest effective dose and shortest duration, as NSAID use may cause fetal renal dysfunction leading to oligohydramnios. Consult your clinician before taking ibuprofen while pregnant.' },
  { label: '6e8c9b9f-1111-4b22-8a4d-123456789abc', drug: 'Warfarin', section: 'Drug Interactions', text: 'Aspirin and other drugs that affect platelet function can increase the risk of bleeding when combined with warfarin. Monitor INR closely when starting or stopping any interacting drug.' },
  { label: '7f9dacaf-2222-4c33-9b5e-23456789abcd', drug: 'Sertraline', section: 'Package Label', text: 'NDC 0378-0231-01. Sertraline hydrochloride tablets 50 mg. 100 tablets. Rx only.' },
]
let flaky503Done = false
const notFound = [
  { time: 1761900000, user: 'anon@demo.local', tenant: null, endpoint: '/search', query: 'nothing', reason: 'no results' },
  { time: 1761990000, user: 'cardio@demo.local', tenant: 'acme', endpoint: '/answer', query: 'moon dust dosing', reason: 'unsupported' },
]
const revoked = new Set()
const sessions = new Map() // token -> {user, exp}

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { 'Content-Type': 'application/json', ...headers })
  res.end(JSON.stringify(body))
}
const readBody = (req) => new Promise((resolve) => {
  let d = ''
  req.on('data', (c) => (d += c))
  req.on('end', () => { try { resolve(JSON.parse(d || '{}')) } catch { resolve(null) } })
})
function issue(user) {
  const token = 'tok_' + Math.random().toString(36).slice(2)
  const exp = Math.floor(Date.now() / 1000) + 3600
  sessions.set(token, { ...user, expires: exp })
  return { token, user: { ...user, expires: exp } }
}
const authed = (req) => {
  const t = (req.headers.authorization || '').replace(/^Bearer /, '')
  const s = sessions.get(t)
  if (!s || revoked.has(t) || revoked.has(s.email)) return null
  return { token: t, user: s }
}

http.createServer(async (req, res) => {
  const url = req.url.split('?')[0]
  const key = `${req.method} ${url}`
  if (key === 'GET /config') return send(res, 200, config)
  if (key === 'GET /healthz') return send(res, 200, { ok: true })
  const body = req.method === 'POST' ? await readBody(req) : {}
  if (body === null) return send(res, 422, { detail: 'Invalid JSON' })

  if (key === 'POST /auth/demo') {
    const u = users[body.persona]
    return u ? send(res, 200, issue(u)) : send(res, 422, { detail: 'Unknown persona' })
  }
  if (key === 'POST /auth/register') {
    if (!body.email || !body.password || body.password.length < 10) return send(res, 422, { detail: 'Password must be at least 10 characters' })
    return send(res, 200, issue({ email: body.email, groups: [], roles: [], tenant: null }))
  }
  if (key === 'POST /auth/login') {
    if (body.password === 'wrongwrongwrong') return send(res, 429, { detail: 'Too many login attempts. Try again later.' }, { 'Retry-After': '30' })
    if (body.password !== 'password123!') return send(res, 401, { detail: 'Incorrect email or password.' })
    return send(res, 200, issue({ email: body.email, groups: [], roles: [], tenant: null }))
  }

  const a = authed(req)
  if (!a) return send(res, 401, { detail: 'Invalid or expired token' })
  const { user } = a
  if (key === 'GET /auth/me') return send(res, 200, user)
  if (key === 'POST /auth/logout') { sessions.delete(a.token); return send(res, 200, { ok: true }) }

  if (key === 'POST /search') {
    if (!body.query || body.query.length > 1000) return send(res, 422, { detail: 'query must be 1..1000 chars' })
    if (!flaky503Done && /flaky/i.test(body.query)) {
      flaky503Done = true
      return send(res, 503, { detail: 'Index updating' }, { 'Retry-After': '1' })
    }
    if (/nothing/i.test(body.query)) return send(res, 200, { results: [] })
    const words = body.query.toLowerCase().split(/\W+/).filter((w) => w.length > 2)
    let r = docs.filter((d) => words.some((w) => (d.text + d.drug).toLowerCase().includes(w)))
    if (!r.length) r = docs
    await new Promise((x) => setTimeout(x, 400))
    return send(res, 200, { results: r.slice(0, body.k || 5) })
  }
  if (key === 'POST /answer') {
    if (/ibuprofen|pregnan/i.test(body.query || '')) {
      return send(res, 200, { supported: true, top_score: 0.82, citations: [
        { sentence: 'Avoid use in pregnant women starting at 30 weeks of gestation.', drug: 'Ibuprofen', section: 'Pregnancy', label: docs[0].label },
      ] })
    }
    return send(res, 200, { supported: false, top_score: 0.213, citations: [] })
  }
  if (url.startsWith('/admin')) {
    if (!user.roles.includes('admin')) return send(res, 403, { detail: 'Admin role required' })
    if (key === 'GET /admin/not-found') return send(res, 200, { rows: notFound })
    if (key === 'POST /admin/revoke') {
      const n = (body.jti ? 1 : 0) + (body.sub ? 1 : 0)
      if (n !== 1) return send(res, 422, { detail: 'Provide exactly one of jti or sub' })
      revoked.add(body.jti || body.sub)
      return send(res, 200, { revoked: body.jti ? { jti: body.jti } : { sub: body.sub } })
    }
  }
  send(res, 404, { detail: 'Not found' })
}).listen(PORT, '127.0.0.1', () => console.log(`mock backend on http://127.0.0.1:${PORT}`))
