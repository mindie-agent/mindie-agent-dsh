// Test-only full native entry harness for configured knowledge domains.
// Uses the real host3.mjs tool registry, real Python sidecar, Service, Engine
// and Store; only the DSH native LLM boundary is stubbed.

import { existsSync } from 'node:fs'
import path from 'node:path'
import { apply } from '../host3.mjs'

// This is a pytest-driven harness, not a standalone node:test file. When the
// Node test runner collects `tests/*.mjs`, skip instead of requiring env vars.
if (process.env.NODE_TEST_CONTEXT) {
  console.log('host_domain_harness.mjs is exercised by pytest; skipping standalone run')
  process.exit(0)
}

const cwd = process.env.DOMAIN_CWD
const operations = JSON.parse(process.env.DOMAIN_OPERATIONS || '[]')
if (!cwd) throw new Error('DOMAIN_CWD is required')
if (!Array.isArray(operations) || operations.length === 0) {
  throw new Error('DOMAIN_OPERATIONS must be a nonempty array')
}

let llmCalls = 0
const tools = new Map()
const disposers = []
const ctx = {
  tools: { register(tool) { tools.set(tool.name, tool) } },
  agents: {},
  llm: {
    async *stream() {
      llmCalls += 1
      yield { type: 'text-delta', index: 0, text: '{}' }
      yield { type: 'usage', usage: { inputTokens: 1, cacheReadTokens: 0, outputTokens: 1 } }
      yield { type: 'finish', reason: { kind: 'stop' } }
    },
  },
  on() {},
  effect(callback) {
    const dispose = callback()
    if (typeof dispose === 'function') disposers.push(dispose)
  },
}

apply(ctx)
const endpointFile = path.join(process.env.MINDIE_DSH_STATE_DIR, 'summary-endpoint.json')
for (let index = 0; index < 200 && !existsSync(endpointFile); index += 1) {
  await new Promise((resolve) => setTimeout(resolve, 25))
}

const session = {
  id: process.env.DOMAIN_SESSION || 'manual-A',
  header: { cwd },
}
const exec = {
  agent: { session, options: { provider: 'stub-provider', model: 'stub-model' } },
  signal: undefined,
}
const entry = tools.get('mindie_entry')
if (!entry) throw new Error('mindie_entry is not registered')
let activated = false
try {
  activated = Boolean(JSON.parse(await entry.execute({ op: 'init' }, exec)).activated)
} catch {
  activated = false
}

const results = []
for (const operation of operations) {
  const selected = tools.get(operation.tool)
  if (!selected) {
    results.push({ ok: false, error: `tool not registered: ${operation.tool}` })
    continue
  }
  try {
    results.push({ ok: true, value: JSON.parse(await selected.execute(operation.args || {}, exec)) })
  } catch (caught) {
    results.push({ ok: false, error: String(caught?.message || caught) })
  }
}

console.log(JSON.stringify({ activated, results, llmCalls }))
for (const dispose of disposers.reverse()) {
  try { dispose() } catch { /* process is exiting */ }
}
process.exit(0)
