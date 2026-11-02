const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// Split text into [{text, hit}] parts, marking words from the query (case-insensitive).
export function highlightParts(text, query) {
  const words = [...new Set(query.split(/\s+/).map((w) => w.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '')).filter((w) => w.length > 1))]
  if (!words.length) return [{ text, hit: false }]
  words.sort((a, b) => b.length - a.length)
  const re = new RegExp(`(${words.map(escapeRegExp).join('|')})`, 'gi')
  return text.split(re).filter(Boolean).map((part, i) => ({ text: part, hit: i % 2 === 1 }))
}

export const dailyMedUrl = (setId) =>
  `https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=${encodeURIComponent(setId)}`
