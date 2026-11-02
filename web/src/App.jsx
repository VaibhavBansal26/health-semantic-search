import { useCallback, useEffect, useState } from 'react'
import * as api from './api.js'
import { initTheme, applyTheme } from './theme.js'
import Login from './components/Login.jsx'
import Header from './components/Header.jsx'
import SearchView from './components/SearchView.jsx'
import AdminView from './components/AdminView.jsx'
import Footer from './components/Footer.jsx'

export default function App() {
  const [theme, setTheme] = useState(initTheme)
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState('')
  const [user, setUser] = useState(null)
  const [booting, setBooting] = useState(true)
  const [notice, setNotice] = useState('')
  const [tab, setTab] = useState('search')

  const dropSession = useCallback((msg) => {
    api.setToken(null)
    setUser(null)
    setTab('search')
    if (msg) setNotice(msg)
  }, [])

  useEffect(() => {
    api.setUnauthorizedHandler(() => dropSession('Your session expired. Please sign in again.'))
    let cancelled = false
    ;(async () => {
      try {
        const cfg = await api.getConfig()
        if (cancelled) return
        setConfig(cfg)
      } catch (e) {
        if (!cancelled) setConfigError(e.message)
      }
      if (api.loadStoredToken()) {
        try {
          const u = await api.me()
          if (!cancelled) setUser(u)
        } catch { /* 401 handler already cleared the session */ }
      }
      if (!cancelled) setBooting(false)
    })()
    return () => { cancelled = true }
  }, [dropSession])

  const onAuthed = ({ token, user }) => {
    api.setToken(token)
    setNotice('')
    setUser(user)
  }

  const signOut = async () => {
    try { await api.logout() } catch { /* drop locally regardless */ }
    dropSession('')
  }

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    setTheme(next)
  }

  const isAdmin = !!user && (user.roles || []).includes('admin')

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to content</a>
      {user && (
        <Header user={user} theme={theme} onToggleTheme={toggleTheme} onSignOut={signOut}
          tab={tab} onTab={setTab} isAdmin={isAdmin} />
      )}
      <main id="main" className="main" tabIndex={-1}>
        {booting && <p className="muted center" role="status">Loading...</p>}
        {!booting && configError && <div className="alert error" role="alert">{configError}</div>}
        {!booting && !user && config && (
          <Login config={config} notice={notice} onAuthed={onAuthed}
            theme={theme} onToggleTheme={toggleTheme} />
        )}
        {!booting && user && config && tab === 'search' && <SearchView config={config} />}
        {!booting && user && isAdmin && tab === 'admin' && <AdminView />}
      </main>
      <Footer notice={config && config.notice} />
    </div>
  )
}
