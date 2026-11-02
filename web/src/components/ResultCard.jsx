import { useState } from 'react'
import { highlightParts, dailyMedUrl } from '../highlight.js'
import { CopyIcon, ExternalIcon } from './icons.jsx'

const SNIPPET = 320

function Highlighted({ text, query }) {
  return highlightParts(text, query).map((p, i) =>
    p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>)
}

// Pick a snippet window around the first matching word when text is long.
function snippet(text, query) {
  if (text.length <= SNIPPET) return text
  const words = query.split(/\s+/).filter((w) => w.length > 2).map((w) => w.toLowerCase())
  const lower = text.toLowerCase()
  const idx = words.map((w) => lower.indexOf(w)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0
  const start = Math.max(0, idx - 80)
  const end = Math.min(text.length, start + SNIPPET)
  return (start > 0 ? '... ' : '') + text.slice(start, end) + (end < text.length ? ' ...' : '')
}

export default function ResultCard({ result, query }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState('')
  const long = result.text.length > SNIPPET
  const shown = open ? result.text : snippet(result.text, query)

  const copy = async () => {
    const out = `${result.drug} - ${result.section} (DailyMed set ID ${result.label})\n\n${result.text}`
    try {
      await navigator.clipboard.writeText(out)
      setCopied('Copied')
    } catch {
      setCopied('Copy failed')
    }
    setTimeout(() => setCopied(''), 2000)
  }

  return (
    <article className="card">
      <header className="card-head">
        <h3>{result.drug}</h3>
        <span className="section">{result.section}</span>
      </header>
      <p className="snippet"><Highlighted text={shown} query={query} /></p>
      <footer className="card-foot">
        {long && (
          <button type="button" className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Show less' : 'Show full text'}
          </button>
        )}
        <button type="button" className="btn small" onClick={copy}>
          <CopyIcon /> Copy with citation
        </button>
        <span className="sr-only" role="status" aria-live="polite">{copied}</span>
        {copied && <span className="copied" aria-hidden="true">{copied}</span>}
        <a className="label-link" href={dailyMedUrl(result.label)} target="_blank" rel="noopener noreferrer">
          {result.label} <ExternalIcon /><span className="sr-only"> (opens DailyMed in a new tab)</span>
        </a>
      </footer>
    </article>
  )
}
