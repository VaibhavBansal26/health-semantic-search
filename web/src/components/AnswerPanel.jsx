import { useState } from 'react'
import * as api from '../api.js'
import { dailyMedUrl } from '../highlight.js'

export default function AnswerPanel({ query }) {
  const [state, setState] = useState({ status: 'idle' })

  const ask = async () => {
    setState({ status: 'loading' })
    try {
      setState({ status: 'done', data: await api.answer(query) })
    } catch (e) {
      setState({ status: 'error', message: e.message })
    }
  }

  const { status, data } = state
  return (
    <section className="answer card" aria-label="Answer with citations">
      <div className="answer-head">
        <button type="button" className="btn" onClick={ask} disabled={status === 'loading'}>
          {status === 'loading' ? 'Finding answer...' : 'Answer with citations'}
        </button>
        <span className="hint">Quotes sentences from the labels; never writes its own.</span>
      </div>
      <div aria-live="polite">
        {status === 'error' && <div className="alert error" role="alert">{state.message}</div>}
        {status === 'done' && data.supported && (
          <ul className="citations">
            {data.citations.map((c, i) => (
              <li key={i}>
                <blockquote>{c.sentence}</blockquote>
                <span className="cite-meta">
                  {c.drug} - {c.section} -{' '}
                  <a href={dailyMedUrl(c.label)} target="_blank" rel="noopener noreferrer">{c.label}</a>
                </span>
              </li>
            ))}
          </ul>
        )}
        {status === 'done' && !data.supported && (
          <div className="refusal">
            <p><strong>No supported answer found</strong></p>
            <p className="hint">
              This is a deliberate refusal, not a guess: no label sentence was similar enough to your
              question to cite safely. (top score: {Number(data.top_score).toFixed(3)})
            </p>
          </div>
        )}
      </div>
    </section>
  )
}
