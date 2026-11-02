import { useState } from 'react'
import * as api from '../api.js'
import { EyeIcon, EyeOffIcon } from './icons.jsx'
import { ThemeToggle } from './Header.jsx'

export default function Login({ config, notice, onAuthed, theme, onToggleTheme }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const canRegister = !!config.registration
  const personas = config.demo_personas || []

  const run = async (fn) => {
    setBusy(true)
    setError('')
    try {
      onAuthed(await fn())
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  const submit = (e) => {
    e.preventDefault()
    if (mode === 'register' && password.length < 10) {
      setError('Password must be at least 10 characters.')
      return
    }
    run(() => (mode === 'register' ? api.register(email.trim(), password) : api.login(email.trim(), password)))
  }

  return (
    <section className="login card" aria-labelledby="login-title">
      <div className="login-top">
        <h1 id="login-title" className="brand">Health Label Search</h1>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
      <p className="muted">Search public FDA drug-label text by meaning, not just keywords.</p>

      <div aria-live="polite">
        {notice && <div className="alert info">{notice}</div>}
        {error && <div className="alert error" role="alert">{error}</div>}
      </div>

      {canRegister && (
        <div className="segmented" role="tablist" aria-label="Account">
          {[['login', 'Sign in'], ['register', 'Create account']].map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={mode === id}
              className={mode === id ? 'seg active' : 'seg'}
              onClick={() => { setMode(id); setError('') }}>{label}</button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="form">
        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="email" required value={email}
          onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="password">Password</label>
        <div className="pw">
          <input id="password" type={show ? 'text' : 'password'} required
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            minLength={mode === 'register' ? 10 : undefined}
            aria-describedby={mode === 'register' ? 'pw-hint' : undefined}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          <button type="button" className="btn icon-btn" onClick={() => setShow(!show)}
            aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}>
            {show ? <EyeOffIcon /> : <EyeIcon />}
          </button>
        </div>
        {mode === 'register' && <p id="pw-hint" className="hint">At least 10 characters.</p>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Please wait...' : mode === 'register' ? 'Create account' : 'Sign in'}
        </button>
      </form>

      {personas.length > 0 && (
        <div className="demo">
          <h2>Try a demo account</h2>
          <p className="hint">No sign-up needed. Each demo account sees a different set of labels.</p>
          <ul className="persona-list">
            {personas.map((p) => (
              <li key={p.id}>
                <button type="button" className="persona" disabled={busy}
                  onClick={() => run(() => api.demoLogin(p.id))}>
                  <strong>{p.label}</strong>
                  <span>{p.description}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
