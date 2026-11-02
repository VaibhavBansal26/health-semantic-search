import { useRef, useState } from 'react'
import * as api from '../api.js'
import ResultCard from './ResultCard.jsx'
import AnswerPanel from './AnswerPanel.jsx'
import { SearchIcon } from './icons.jsx'

const MODE_LABELS = {
  keyword: 'Keyword',
  dense: 'Meaning',
  hybrid: 'Hybrid (recommended)',
  rerank: 'Hybrid + reranker',
}
const EXAMPLES = ['Can I take ibuprofen while pregnant?', 'warfarin and aspirin interaction', 'NDC 0378-0231']

export default function SearchView({ config }) {
  const modes = (config.modes && config.modes.length ? config.modes : ['hybrid'])
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState(modes.includes('hybrid') ? 'hybrid' : modes[0])
  const [state, setState] = useState({ status: 'idle' }) // idle | loading | done | error
  const seq = useRef(0)

  const run = async (q, m = mode) => {
    q = q.trim()
    if (!q) return
    const id = ++seq.current
    setState({ status: 'loading', query: q })
    const t0 = performance.now()
    try {
      const data = await api.search(q, m, 5)
      if (id !== seq.current) return
      setState({ status: 'done', query: q, results: data.results || [], ms: Math.round(performance.now() - t0) })
    } catch (e) {
      if (id !== seq.current) return
      setState({ status: 'error', query: q, message: e.message })
    }
  }

  const onSubmit = (e) => { e.preventDefault(); run(query) }
  const pick = (q) => { setQuery(q); run(q) }
  const changeMode = (m) => { setMode(m); if (state.query) run(state.query, m) }

  return (
    <div className="search-view">
      <form onSubmit={onSubmit} role="search" className="search-form">
        <label htmlFor="q" className="sr-only">Search query</label>
        <div className="search-box">
          <input id="q" type="search" maxLength={1000} value={query} autoComplete="off"
            placeholder="Ask about a drug, side effect, interaction or NDC"
            onChange={(e) => setQuery(e.target.value)} />
          <button type="submit" className="btn primary" disabled={!query.trim() || state.status === 'loading'}>
            <SearchIcon /> <span>Search</span>
          </button>
        </div>
      </form>

      <div className="controls">
        <div className="segmented" role="radiogroup" aria-label="Search mode">
          {modes.map((m) => (
            <label key={m} className={mode === m ? 'seg active' : 'seg'}>
              <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => changeMode(m)} />
              {MODE_LABELS[m] || m}
            </label>
          ))}
        </div>
        <ul className="chips" aria-label="Example queries">
          {EXAMPLES.map((ex) => (
            <li key={ex}><button type="button" className="chip" onClick={() => pick(ex)}>{ex}</button></li>
          ))}
        </ul>
      </div>

      {config.answers && state.query && state.status !== 'loading' && (
        <AnswerPanel key={state.query} query={state.query} />
      )}

      <div aria-live="polite" aria-busy={state.status === 'loading'} className="results">
        {state.status === 'loading' && <Skeleton />}
        {state.status === 'error' && <div className="alert error" role="alert">{state.message}</div>}
        {state.status === 'done' && (
          <>
            <p className="meta">
              {state.results.length} result{state.results.length === 1 ? '' : 's'} in {state.ms} ms
            </p>
            {state.results.length === 0 ? (
              <div className="empty">
                <p><strong>No readable result.</strong> Your account may not have access to labels that match.</p>
                <p className="hint">Try different wording, a drug name, or switch to another demo account with wider access.</p>
              </div>
            ) : (
              <ul className="cards">
                {state.results.map((r, i) => (
                  <li key={`${r.label}-${r.section}-${i}`}><ResultCard result={r} query={state.query} /></li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Skeleton() {
  return (
    <div role="status" aria-label="Searching">
      <span className="sr-only">Searching...</span>
      {[0, 1, 2].map((i) => (
        <div className="card skeleton" key={i} aria-hidden="true">
          <div className="sk line w40" /><div className="sk line" /><div className="sk line w80" />
        </div>
      ))}
    </div>
  )
}
