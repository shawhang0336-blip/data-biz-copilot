import React, { useEffect, useState } from 'react'
import { getBudget, getLlmConfig, updateBudget, updateLlmConfig } from '../../api/platform.js'
import { BUDGET_LABELS } from '../../utils/labels.js'

export default function SettingsPage() {
  const [budget, setBudget] = useState(null)
  const [llmCfg, setLlmCfg] = useState(null)
  const [saved, setSaved] = useState('')
  const [llmSaved, setLlmSaved] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getBudget().then((res) => setBudget(res.budget)).catch((e) => setError(e.message))
    getLlmConfig().then((res) => setLlmCfg(res.config)).catch((e) => setError(e.message))
  }, [])

  const handleSaveBudget = async () => {
    setSaved(''); setError('')
    try {
      const res = await updateBudget(budget)
      setBudget(res.budget)
      setSaved('执行预算设置已更新')
    } catch (e) {
      setError(e.message)
    }
  }

  const handleSaveLlm = async () => {
    setLlmSaved(''); setError('')
    try {
      const res = await updateLlmConfig(llmCfg)
      setLlmCfg(res.config)
      setLlmSaved('大模型语义翻译与问答配置已保存（将受去技术化门禁保护后生效）')
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div>
      <div className="card">
        <h2>大模型语义理解与翻译配置</h2>
        <p style={{ color: '#475569', fontSize: 14 }}>
          当面对陌生 SQL 代码逻辑或自然语言提问时，可切换为 OpenAI 兼容大模型执行“代码 AST → 纯业务中文”受控翻译（受 Bus 门禁校验）。
        </p>
        {llmCfg && (
          <div className="grid-2" style={{ marginTop: 12 }}>
            <div>
              <label style={{ fontSize: 13, fontWeight: 600 }}>运行模式</label>
              <select
                value={llmCfg.llm_provider}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_provider: e.target.value })}
              >
                <option value="scripted">离线确定性引擎（无需模型密钥）</option>
                <option value="deepseek">DeepSeek（deepseek-chat / deepseek-flash）</option>
                <option value="zhipu">智谱清言（GLM 系列）</option>
                <option value="openai_compatible">其他 OpenAI 兼容模型</option>
              </select>
            </div>
            <div>
              <label style={{ fontSize: 13, fontWeight: 600 }}>模型名称（Model）</label>
              <input
                className="input"
                value={llmCfg.llm_model}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_model: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 13, fontWeight: 600 }}>接口地址（Base URL）</label>
              <input
                className="input"
                value={llmCfg.llm_base_url}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_base_url: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 13, fontWeight: 600 }}>API Key</label>
              <input
                className="input"
                type="password"
                placeholder={llmCfg.has_api_key ? '已保存密钥（留空则保持不变）' : '输入模型 API Key'}
                value={llmCfg.llm_api_key}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_api_key: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 13, fontWeight: 600 }}>单次调用超时秒数</label>
              <input
                className="input"
                type="number"
                value={llmCfg.llm_timeout}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_timeout: Number(e.target.value) })}
              />
            </div>
          </div>
        )}
        <div style={{ marginTop: 14 }}>
          <button className="btn btn-primary" onClick={handleSaveLlm} disabled={!llmCfg}>保存模型配置</button>
        </div>
        {llmSaved && <p style={{ color: '#047857', marginTop: 10, fontWeight: 600 }}>{llmSaved}</p>}
      </div>

      <div className="card">
        <h2>运行预算与治理门禁设置</h2>
        <p style={{ color: '#475569', fontSize: 14 }}>控制并发处理规模与智能调用成本上限，保障解析与问答稳定可控。</p>
        {budget && (
          <div className="grid-2" style={{ marginTop: 12 }}>
            {Object.entries(budget).map(([k, v]) => (
              <div key={k}>
                <label style={{ fontSize: 13, fontWeight: 600 }}>{BUDGET_LABELS[k] || k}</label>
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
          <button className="btn btn-primary" onClick={handleSaveBudget} disabled={!budget}>保存预算设置</button>
        </div>
        {saved && <p style={{ color: '#047857', marginTop: 10 }}>{saved}</p>}
        {error && <p style={{ color: '#b91c1c', marginTop: 10 }}>{error}</p>}
      </div>
    </div>
  )
}
