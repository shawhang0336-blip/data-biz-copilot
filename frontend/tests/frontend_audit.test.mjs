import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { ApiError, api, formatApiErrorDetail } from '../src/api/client.js'
import { COVERAGE_LABELS, RECONCILE_LABELS, RULE_CATEGORY_LABELS, formatBadge } from '../src/utils/labels.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC_DIR = path.resolve(__dirname, '../src')

function walkFiles(dir) {
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walkFiles(full))
    else out.push(full)
  }
  return out
}

test('ApiError formats FastAPI 422 array detail into readable Chinese', () => {
  const err = new ApiError(422, [{ loc: ['body', 'query'], msg: 'Field required' }])
  assert.equal(err.status, 422)
  assert.match(err.message, /query: Field required/)
  assert.equal(formatApiErrorDetail(null, 502), '请求失败（502）')
})

test('api() omits Content-Type on GET without body', async () => {
  const origFetch = globalThis.fetch
  let captured = null
  try {
    globalThis.fetch = async (_url, init) => {
      captured = init
      return { ok: true, status: 200, text: async () => JSON.stringify({ status: 'ok' }) }
    }
    const res = await api('/api/health')
    assert.deepEqual(res, { status: 'ok' })
    assert.equal(captured.headers['Content-Type'], undefined)
  } finally {
    globalThis.fetch = origFetch
  }
})

test('labels use pure business language without technical SQL/table jargon', () => {
  const allTexts = [
    ...Object.values(COVERAGE_LABELS).map((x) => x.label),
    ...Object.values(RECONCILE_LABELS).map((x) => x.label),
    ...Object.values(RULE_CATEGORY_LABELS),
  ].join(' ')
  assert.doesNotMatch(allTexts, /SQL|SELECT|WHERE|JOIN|TMP_|ODS_|物理表|存储过程/i)
  assert.equal(formatBadge(RECONCILE_LABELS, 'UNREALIZED').label, '当前系统尚未包含')
})

test('frontend architecture guard: 4 domains symmetric and fetch isolated in src/api/', () => {
  const topDirs = fs.readdirSync(SRC_DIR, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name)
  assert.deepEqual(topDirs.sort(), ['api', 'app', 'features', 'styles', 'utils'])

  const featureDirs = fs.readdirSync(path.join(SRC_DIR, 'features'), { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort()
  assert.deepEqual(featureDirs, ['catalog', 'copilot', 'ingestion', 'platform'])

  const nonApiFiles = walkFiles(SRC_DIR).filter((f) => !f.includes(`${path.sep}api${path.sep}`))
  for (const file of nonApiFiles) {
    const content = fs.readFileSync(file, 'utf-8')
    assert.doesNotMatch(content, /\bfetch\s*\(/, `Direct fetch() forbidden outside src/api/: ${file}`)
  }
})
