import React, { useEffect, useState } from 'react'
import { getBudget, getLlmConfig, updateBudget, updateLlmConfig } from '../../api/platform.js'
import { BUDGET_LABELS } from '../../utils/labels.js'

const CONCURRENCY_KEYS = ['max_llm_concurrency', 'max_llm_rpm', 'max_retries', 'max_elapsed_seconds']
const QUOTA_KEYS = ['max_calls_per_script', 'max_llm_calls', 'max_total_tokens']

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
    setSaved('')
    setError('')
    try {
      const res = await updateBudget(budget)
      setBudget(res.budget)
      setSaved('运行预算设置已更新')
    } catch (e) {
      setError(e.message)
    }
  }

  const handleSaveLlm = async () => {
    setLlmSaved('')
    setError('')
    try {
      const res = await updateLlmConfig(llmCfg)
      setLlmCfg(res.config)
      setLlmSaved('大模型服务配置已保存')
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="settings-column">
      <div className="page-header">
        <div>
          <h1 className="page-title">系统配置</h1>
          <p className="page-subtitle">
            配置大模型服务端点、并发频率限制与任务额度保护，保障材料解析与智能问答稳定可控。
          </p>
        </div>
      </div>

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 16 }}>大模型服务配置</h2>
            <p style={{ color: '#64748b', fontSize: 13, margin: '4px 0 0' }}>
              可选择离线确定性引擎或接入 OpenAI 兼容大模型执行业务口径提炼与智能问答。
            </p>
          </div>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleSaveLlm} disabled={!llmCfg}>
            保存模型配置
          </button>
        </div>

        {llmCfg && (
          <div className="grid-2">
            <div>
              <label className="form-label">运行模式</label>
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
              <label className="form-label">模型名称（Model）</label>
              <input
                className="input"
                value={llmCfg.llm_model}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_model: e.target.value })}
              />
            </div>
            <div>
              <label className="form-label">接口地址（Base URL）</label>
              <input
                className="input"
                value={llmCfg.llm_base_url}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_base_url: e.target.value })}
              />
            </div>
            <div>
              <label className="form-label">API Key</label>
              <input
                className="input"
                type="password"
                placeholder={llmCfg.has_api_key ? '已保存密钥（留空则保持不变）' : '输入模型 API Key'}
                value={llmCfg.llm_api_key}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_api_key: e.target.value })}
              />
            </div>
            <div>
              <label className="form-label">单次调用超时（秒）</label>
              <input
                className="input"
                type="number"
                value={llmCfg.llm_timeout}
                onChange={(e) => setLlmCfg({ ...llmCfg, llm_timeout: Number(e.target.value) })}
              />
            </div>
          </div>
        )}
        {llmSaved && <div className="alert-banner success">{llmSaved}</div>}
      </div>

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 16 }}>运行预算与并发控制</h2>
            <p style={{ color: '#64748b', fontSize: 13, margin: '4px 0 0' }}>
              控制批处理并发规模、请求频率与单任务资源消耗上限。
            </p>
          </div>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleSaveBudget} disabled={!budget}>
            保存预算设置
          </button>
        </div>

        {budget && (
          <>
            <div className="section-subhead">并发与频率限制</div>
            <div className="grid-2">
              {CONCURRENCY_KEYS.filter((k) => k in budget).map((k) => (
                <div key={k}>
                  <label className="form-label">{BUDGET_LABELS[k] || k}</label>
                  <input
                    className="input"
                    type="number"
                    value={budget[k]}
                    onChange={(e) => setBudget({ ...budget, [k]: Number(e.target.value) })}
                  />
                </div>
              ))}
            </div>

            <div className="section-subhead">任务额度保护</div>
            <div className="grid-2">
              {QUOTA_KEYS.filter((k) => k in budget).map((k) => (
                <div key={k}>
                  <label className="form-label">{BUDGET_LABELS[k] || k}</label>
                  <input
                    className="input"
                    type="number"
                    value={budget[k]}
                    onChange={(e) => setBudget({ ...budget, [k]: Number(e.target.value) })}
                  />
                </div>
              ))}
            </div>
          </>
        )}
        {saved && <div className="alert-banner success">{saved}</div>}
        {error && <div className="alert-banner error">{error}</div>}
      </div>
    </div>
  )
}
