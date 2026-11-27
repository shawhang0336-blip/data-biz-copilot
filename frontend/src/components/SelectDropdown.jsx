import React, { useEffect, useId, useMemo, useRef, useState } from 'react'

export default function SelectDropdown({
  value,
  options = [],
  onChange,
  placeholder = '请选择',
  ariaLabel,
  disabled = false,
  size = 'md',
  align = 'left',
  className = '',
  minWidth,
}) {
  const [open, setOpen] = useState(false)
  const [highlighted, setHighlighted] = useState(-1)
  const rootRef = useRef(null)
  const listRef = useRef(null)
  const listboxId = useId()

  const selectedIndex = useMemo(
    () => options.findIndex((opt) => String(opt.value) === String(value)),
    [options, value],
  )
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null

  useEffect(() => {
    if (!open) return undefined
    function handleOutside(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    function handleEsc(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [open])

  useEffect(() => {
    if (open) {
      setHighlighted(selectedIndex >= 0 ? selectedIndex : 0)
    }
  }, [open, selectedIndex])

  useEffect(() => {
    if (!open || highlighted < 0 || !listRef.current) return
    const itemEl = listRef.current.children[highlighted]
    if (itemEl && typeof itemEl.scrollIntoView === 'function') {
      itemEl.scrollIntoView({ block: 'nearest' })
    }
  }, [open, highlighted])

  function handleSelect(opt) {
    if (!opt || opt.disabled) return
    setOpen(false)
    if (String(opt.value) !== String(value)) {
      onChange?.(opt.value, opt)
    }
  }

  function handleKeyDown(e) {
    if (disabled) return
    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
        e.preventDefault()
        setOpen(true)
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlighted((prev) => (prev + 1) % Math.max(options.length, 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlighted((prev) => (prev - 1 + options.length) % Math.max(options.length, 1))
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (options[highlighted]) handleSelect(options[highlighted])
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <div
      ref={rootRef}
      className={`ui-dropdown ui-dropdown-${size} ${open ? 'is-open' : ''} ${className}`.trim()}
      style={minWidth ? { minWidth } : undefined}
    >
      <button
        type="button"
        className="ui-dropdown-trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        disabled={disabled}
        onClick={() => !disabled && setOpen((prev) => !prev)}
        onKeyDown={handleKeyDown}
      >
        <span className="ui-dropdown-value">
          {selected ? (
            <>
              {selected.badge && <span className="ui-dropdown-badge">{selected.badge}</span>}
              <span className="ui-dropdown-label" title={selected.label}>{selected.label}</span>
              {selected.meta && <span className="ui-dropdown-meta">{selected.meta}</span>}
            </>
          ) : (
            <span className="ui-dropdown-placeholder">{placeholder}</span>
          )}
        </span>
        <svg className="ui-dropdown-chevron" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M4.25 6.25L8 10L11.75 6.25" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div
          id={listboxId}
          ref={listRef}
          role="listbox"
          aria-label={ariaLabel}
          className={`ui-dropdown-menu ${align === 'right' ? 'align-right' : ''}`}
        >
          {options.length === 0 ? (
            <div className="ui-dropdown-empty">暂无可选项</div>
          ) : (
            options.map((opt, idx) => {
              const isSelected = String(opt.value) === String(value)
              const isHighlighted = idx === highlighted
              return (
                <button
                  key={`${opt.value}-${idx}`}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  disabled={opt.disabled}
                  className={`ui-dropdown-option ${isSelected ? 'is-selected' : ''} ${isHighlighted ? 'is-highlighted' : ''}`}
                  onMouseEnter={() => setHighlighted(idx)}
                  onClick={() => handleSelect(opt)}
                >
                  <div className="ui-dropdown-option-main">
                    <div className="ui-dropdown-option-top">
                      {opt.badge && <span className="ui-dropdown-badge">{opt.badge}</span>}
                      <span className="ui-dropdown-option-label">{opt.label}</span>
                      {opt.tag && <span className="ui-dropdown-tag">{opt.tag}</span>}
                    </div>
                    {opt.description && <span className="ui-dropdown-option-desc">{opt.description}</span>}
                  </div>
                  <div className="ui-dropdown-option-right">
                    {opt.meta && <span className="ui-dropdown-option-meta">{opt.meta}</span>}
                    {isSelected && (
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                        <path d="M3.5 8.5L6.5 11.5L12.5 4.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </div>
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
