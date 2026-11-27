import React, { useEffect, useState } from 'react'
import { getHealth } from '../api/platform.js'

export default function BackendHealthPill() {
  const [state, setState] = useState({ ok: true, checking: true, runs: 0 })

  useEffect(() => {
    let active = true
    async function probe() {
      try {
        const res = await getHealth()
        if (active) setState({ ok: res?.status === 'ok', checking: false, runs: res?.runs || 0 })
      } catch {
        if (active) setState({ ok: false, checking: false, runs: 0 })
      }
    }
    probe()
    const timer = window.setInterval(probe, 25000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  return (
    <span
      className={`dbc-health-pill ${state.checking ? 'is-checking' : state.ok ? 'is-ok' : 'is-down'}`}
      title={state.ok ? `后端在线（累计 ${state.runs} 条运行账本）` : '无法连接后端服务，请检查服务进程'}
    >
      <span className="dbc-health-dot" aria-hidden="true" />
      <span>{state.checking ? '连接检查中' : state.ok ? '服务在线' : '服务离线'}</span>
    </span>
  )
}
