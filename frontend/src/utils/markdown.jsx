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

export function MarkdownText({ content }) {
  const lines = String(content || '').split(/\r?\n/)
  const elements = []
  let listItems = []
  let orderedItems = []

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
    const trimmed = rawLine.trim()
    if (!trimmed) {
      flushLists(idx)
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
      elements.push(<h4 key={`h4-${idx}`} style={{ margin: '10px 0 6px' }}>{renderInline(trimmed.slice(4), `h4-${idx}`)}</h4>)
    } else if (trimmed.startsWith('## ')) {
      elements.push(<h3 key={`h3-${idx}`} style={{ margin: '12px 0 6px' }}>{renderInline(trimmed.slice(3), `h3-${idx}`)}</h3>)
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
