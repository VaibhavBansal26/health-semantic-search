import { useCallback, useEffect, useState } from 'react'
import * as api from '../api.js'

const fmtTime = (t) => {
  const d = typeof t === 'number' ? new Date(t * 1000) : new Date(t)
  return isNaN(d) ? String(t) : d.toLocaleString()
}
const ts = (t) => {
  const d = typeof t === 'number' ? t * 1000 : Date.parse(t)
  return isNaN(d) ? 0 : d
}

export default function AdminView() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.adminNotFound()
      setRows([...(data.rows || [])].sort((a, b) => ts(b.time) - ts(a.time)))
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="admin">
      <section className="card" aria-labelledby="nf-title">
        <div className="section-head">
          <h2 id="nf-title">Queries with no result</h2>
          <button type="button" className="btn" onClick={load} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
        <div aria-live="polite">
          {error && <div className="alert error" role="alert">{error}</div>}
          {!error && !loading && rows.length === 0 && <p className="muted">No not-found queries recorded.</p>}
        </div>
        {rows.length > 0 && (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Not-found queries table">
            <table>
              <thead>
                <tr><th>Time</th><th>User</th><th>Tenant</th><th>Endpoint</th><th>Query</th><th>Reason</th></tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{fmtTime(r.time)}</td><td>{r.user}</td><td>{r.tenant || '-'}</td>
                    <td>{r.endpoint}</td><td className="wrap">{r.query}</td><td>{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <RevokeForm />
    </div>
  )
}

function RevokeForm() {
  const [by, setBy] = useState('jti')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    const v = value.trim()
    if (!v) return
    setBusy(true)
    setMsg(null)
    try {
      const res = await api.adminRevoke(by === 'jti' ? { jti: v } : { sub: v })
      setMsg({ ok: true, text: `Revoked: ${JSON.stringify(res.revoked ?? res)}` })
      setValue('')
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    }
    setBusy(false)
  }

  return (
    <section className="card" aria-labelledby="rv-title">
      <h2 id="rv-title">Revoke access</h2>
      <form onSubmit={submit} className="form">
        <div className="segmented" role="radiogroup" aria-label="Revoke by">
          {[['jti', 'Token ID'], ['sub', 'User email']].map(([id, label]) => (
            <label key={id} className={by === id ? 'seg active' : 'seg'}>
              <input type="radio" name="by" checked={by === id} onChange={() => setBy(id)} />{label}
            </label>
          ))}
        </div>
        <label htmlFor="rv">{by === 'jti' ? 'Token ID (jti)' : 'User email'}</label>
        <input id="rv" type={by === 'sub' ? 'email' : 'text'} required value={value}
          onChange={(e) => setValue(e.target.value)} />
        <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Revoking...' : 'Revoke'}</button>
      </form>
      <div aria-live="polite">
        {msg && <div className={msg.ok ? 'alert ok' : 'alert error'} role={msg.ok ? 'status' : 'alert'}>{msg.text}</div>}
      </div>
    </section>
  )
}
