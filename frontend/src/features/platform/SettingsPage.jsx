import React, { useEffect, useState } from 'react'
import { getBudget, updateBudget } from '../../api/platform.js'

export default function SettingsPage() {
  const [budget, setBudget] = useState(null)
  const [saved, setSaved] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getBudget().then((res) => setBudget(res.budget)).catch((e) => setError(e.message))
  }, [])

  const handleSave = async () => {
    setSaved(''); setError('')
    try {
      const res = await updateBudget(budget)
      setBudget(res.budget)
      setSaved('执行预算设置已更新')
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="card">
      <h2>运行预算与治理门禁设置</h2>
      <p style={{ color: '#475569', fontSize: 14 }}>控制并发处理规模与智能调用成本上限，保障问答与合成稳定可控。</p>
      {budget && (
        <div className="grid-2" style={{ marginTop: 12 }}>
          {Object.entries(budget).map(([k, v]) => (
            <div key={k}>
              <label style={{ fontSize: 13, fontWeight: 600 }}>{k}</label>
              <input
                className="input"
                type="number"
                value={v}
                onChange={(e) => setBudget({ ...budget, [k]: Number(e.target.value) })}
              />
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 14 }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={!budget}>保存预算设置</button>
      </div>
      {saved && <p style={{ color: '#047857', marginTop: 10 }}>{saved}</p>}
      {error && <p style={{ color: '#b91c1c', marginTop: 10 }}>{error}</p>}
    </div>
  )
}
