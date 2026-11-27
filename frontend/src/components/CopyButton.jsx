import React, { useEffect, useRef, useState } from 'react'

export default function CopyButton({ text, label = '复制', copiedLabel = '已复制', className = 'btn btn-sm' }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => () => {
    if (timerRef.current) window.clearTimeout(timerRef.current)
  }, [])

  async function handleCopy() {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      document.body.removeChild(area)
    }
    setCopied(true)
    if (timerRef.current) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => setCopied(false), 1800)
  }

  return (
    <button
      type="button"
      className={`${className} copy-btn${copied ? ' copied' : ''}`}
      disabled={!text}
      onClick={handleCopy}
    >
      {copied ? `✓ ${copiedLabel}` : label}
    </button>
  )
}
