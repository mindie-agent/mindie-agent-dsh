// Test-only full entry harness: real host3.mjs tool registry and summary
// handler, real Python sidecar/Service/Engine via MINDIE_DSH_* paths, with
// only the DSH native LLM boundary stubbed.

import { apply } from '../host3.mjs'
import { distillCallTimeoutMs } from '../scripts/knowledge_tools.mjs'

// This is a pytest-driven harness, not a standalone node:test file. When the
// Node test runner collects `tests/*.mjs`, skip instead of requiring env vars.
if (process.env.NODE_TEST_CONTEXT) {
  console.log('host_tool_harness.mjs is exercised by pytest; skipping standalone run')
  process.exit(0)
}

const decision = process.env.DISTILL_DECISION
const inputs = JSON.parse(process.env.DISTILL_INPUTS || '[]')
const cwd = process.env.DISTILL_CWD
const domain = process.env.DISTILL_DOMAIN
const relatedRefs = process.env.DISTILL_RELATED_REFS
const proposal = process.env.DISTILL_PROPOSAL
if (!decision) throw new Error('DISTILL_DECISION is required')
if (!cwd) throw new Error('DISTILL_CWD is required')

let llmCalls = 0
let promptHasDecisionSchema = false
const tools = new Map()
const listeners = new Map()
const disposers = []
const ctx = {
  tools: { register(tool) { tools.set(tool.name, tool) } },
  agents: {},
  llm: {
    async *stream(options) {
      llmCalls += 1
      if (process.env.DISTILL_MODEL_MODE === 'error') throw new Error('controlled native model failure')
      const prompt = options?.messages?.[0]?.content?.[0]?.text || ''
      promptHasDecisionSchema = prompt.includes('mindie-distill-decision/1')
      yield { type: 'text-delta', index: 0, text: process.env.DISTILL_MODEL_MODE === 'invalid' ? '{}' : decision }
      yield { type: 'usage', usage: { inputTokens: 11, cacheReadTokens: 3, outputTokens: 7 } }
      yield { type: 'finish', reason: { kind: 'stop' } }
    },
  },
  on(event, callback) { listeners.set(event, callback) },
  effect(callback) {
    const dispose = callback()
    if (typeof dispose === 'function') disposers.push(dispose)
  },
}

apply(ctx)

const session = {
  id: 'manual-A',
  header: { cwd },
}
const exec = {
  agent: { session, options: { provider: 'stub-provider', model: 'stub-model' } },
  signal: undefined,
}
const entry = tools.get('mindie_entry')
const distill = tools.get('knowledge_distill')
if (!entry || !distill) throw new Error('required host tools are not registered')
let activated = { activated: false }
if (process.env.DISTILL_PRE_STEP_GESTURE === '1' || process.env.DISTILL_SKIP_ACTIVATION !== '1') {
  const preStep = listeners.get('agent/pre-step')
  if (typeof preStep !== 'function') throw new Error('agent/pre-step listener is not registered')
  const userMsg = {
    source: { kind: 'user' },
    content: [{ type: 'text', text: '/mindie-agent\n\ndistill task' }],
  }
  await preStep(
    { agent: exec.agent, messages: [userMsg] },
    async () => ({ kind: 'enter', messages: [userMsg] }),
  )
}
if (process.env.DISTILL_SKIP_ACTIVATION !== '1') {
  activated = JSON.parse(await entry.execute({ op: 'init' }, exec))
}
const arguments_ = { inputs, task_state: 'verified_milestone' }
if (domain) arguments_.domain = domain
if (relatedRefs) arguments_.related_refs = JSON.parse(relatedRefs)
if (proposal) arguments_.proposal = JSON.parse(proposal)
let result = null
let error = null
try {
  result = JSON.parse(await distill.execute(arguments_, exec))
} catch (caught) {
  error = String(caught?.message || caught)
}
const schemas = Object.fromEntries([...tools].map(([name, tool]) => [name, tool.parameters]))
console.log(JSON.stringify({
  activated: Boolean(activated.activated), result, error, llmCalls, promptHasDecisionSchema,
  toolTimeoutMs: distillCallTimeoutMs(), schemas,
}))
for (const dispose of disposers.reverse()) {
  try { dispose() } catch { /* process is exiting */ }
}
process.exit(0)
