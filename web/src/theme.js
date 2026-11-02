const KEY = 'hss_theme'

export function getStoredTheme() {
  try { return localStorage.getItem(KEY) } catch { return null }
}

export function systemTheme() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme)
  try { localStorage.setItem(KEY, theme) } catch { /* ignore */ }
}

export function initTheme() {
  const t = getStoredTheme()
  const theme = t === 'dark' || t === 'light' ? t : systemTheme()
  document.documentElement.setAttribute('data-theme', theme)
  return theme
}
