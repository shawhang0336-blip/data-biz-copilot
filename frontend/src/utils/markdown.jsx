import React from 'react'

function renderInline(text, keyPrefix) {
  const parts = String(text || '').split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={`${keyPrefix}-b-${idx}`}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code key={`${keyPrefix}-c-${idx}`} className="inline-code">
          {part.slice(1, -1)}
        </code>
      )
    }
    return <React.Fragment key={`${keyPrefix}-t-${idx}`}>{part}</React.Fragment>
  })
}

function splitTableRow(line) {
  let t = line.trim()
  if (t.startsWith('|')) t = t.slice(1)
  if (t.endsWith('|')) t = t.slice(0, -1)
  return t.split('|').map((c) => c.trim())
}

function isTableSeparator(line) {
  const t = line.trim()
  if (!t.includes('-') || !t.includes('|')) return false
  return splitTableRow(t).every((c) => /^:?-{2,}:?$/.test(c))
}

export function MarkdownText({ content }) {
  const lines = String(content || '').split(/\r?\n/)
  const elements = []
  let listItems = []
  let orderedItems = []
  let skipUntil = -1

  const flushLists = (key) => {
    if (listItems.length > 0) {
      elements.push(
        <ul key={`ul-${key}`} className="md-list">
          {listItems.map((item, idx) => (
            <li key={`li-${key}-${idx}`} style={{ marginLeft: item.indent ? 18 : 0 }}>
              {renderInline(item.text, `li-${key}-${idx}`)}
            </li>
          ))}
        </ul>
      )
      listItems = []
    }
    if (orderedItems.length > 0) {
      elements.push(
        <ol key={`ol-${key}`} className="md-list">
          {orderedItems.map((item, idx) => (
            <li key={`oli-${key}-${idx}`}>{renderInline(item, `oli-${key}-${idx}`)}</li>
          ))}
        </ol>
      )
      orderedItems = []
    }
  }

  lines.forEach((rawLine, idx) => {
    if (idx <= skipUntil) return
    const trimmed = rawLine.trim()
    if (!trimmed) {
      flushLists(idx)
      return
    }
    if (trimmed.startsWith('|') && idx + 1 < lines.length && isTableSeparator(lines[idx + 1])) {
      flushLists(idx)
      const header = splitTableRow(trimmed)
      const body = []
      let j = idx + 2
      while (j < lines.length && lines[j].trim().startsWith('|')) {
        body.push(splitTableRow(lines[j]))
        j += 1
      }
      skipUntil = j - 1
      elements.push(
        <div key={`tbl-${idx}`} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {header.map((h, hi) => (
                  <th key={hi}>{renderInline(h, `th-${idx}-${hi}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, ri) => (
                <tr key={ri}>
                  {header.map((_h, ci) => (
                    <td key={ci}>{renderInline(row[ci] || '', `td-${idx}-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      return
    }
    if (trimmed === '---' || trimmed === '***') {
      flushLists(idx)
      elements.push(<hr key={`hr-${idx}`} className="md-hr" />)
      return
    }
    const bulletMatch = rawLine.match(/^(\s*)[-*•]\s+(.+)$/)
    if (bulletMatch) {
      if (orderedItems.length > 0) flushLists(`pre-${idx}`)
      listItems.push({ indent: bulletMatch[1].length >= 2, text: bulletMatch[2] })
      return
    }
    const orderedMatch = trimmed.match(/^\d+\.\s+(.+)$/)
    if (orderedMatch) {
      if (listItems.length > 0) flushLists(`preo-${idx}`)
      orderedItems.push(orderedMatch[1])
      return
    }
    flushLists(idx)
    if (trimmed.startsWith('### ')) {
      elements.push(
        <h4 key={`h4-${idx}`} className="md-h4">
          {renderInline(trimmed.slice(4), `h4-${idx}`)}
        </h4>
      )
    } else if (trimmed.startsWith('## ')) {
      elements.push(
        <h3 key={`h3-${idx}`} className="md-h3">
          {renderInline(trimmed.slice(3), `h3-${idx}`)}
        </h3>
      )
    } else if (trimmed.startsWith('# ')) {
      elements.push(
        <h3 key={`h1-${idx}`} className="md-h3">
          {renderInline(trimmed.slice(2), `h1-${idx}`)}
        </h3>
      )
    } else {
      elements.push(
        <p key={`p-${idx}`} className="md-para">
          {renderInline(trimmed, `p-${idx}`)}
        </p>
      )
    }
  })
  flushLists('end')
  return <div className="md-body">{elements}</div>
}
