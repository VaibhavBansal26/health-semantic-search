import { SunIcon, MoonIcon, InfoIcon } from './icons.jsx'

export function ThemeToggle({ theme, onToggle }) {
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button type="button" className="btn icon-btn" onClick={onToggle}
      aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}>
      {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
    </button>
  )
}

export default function Header({ user, theme, onToggleTheme, onSignOut, tab, onTab, isAdmin }) {
  const groups = user.groups || []
  const viewing = groups.length ? groups.join(', ') : 'public labels only'
  return (
    <header className="header">
      <div className="header-row">
        <h1 className="brand">Health Label Search</h1>
        <div className="header-actions">
          <span className="email" title={user.email}>{user.email}</span>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <button type="button" className="btn" onClick={onSignOut}>Sign out</button>
        </div>
      </div>
      <div className="header-row sub">
        <span className="badge" tabIndex={0} aria-describedby="viewing-tip">
          Viewing as: {viewing}
          <InfoIcon />
          <span role="tooltip" id="viewing-tip" className="tooltip">
            The labels themselves are public FDA text. The access groups are synthetic, made up
            for this demo to show how results can be filtered per user.
          </span>
        </span>
        {isAdmin && (
          <nav className="tabs" aria-label="Sections">
            <button type="button" className={tab === 'search' ? 'tab active' : 'tab'}
              aria-current={tab === 'search' ? 'page' : undefined} onClick={() => onTab('search')}>Search</button>
            <button type="button" className={tab === 'admin' ? 'tab active' : 'tab'}
              aria-current={tab === 'admin' ? 'page' : undefined} onClick={() => onTab('admin')}>Admin</button>
          </nav>
        )}
      </div>
    </header>
  )
}
