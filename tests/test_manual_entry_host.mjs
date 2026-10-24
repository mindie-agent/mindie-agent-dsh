import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { apply } from '../host3.mjs'
import { hasMindieGesture } from '../scripts/knowledge_tools.mjs'

const root = mkdtempSync(path.join(tmpdir(), 'mindie-passive-session-'))
process.env.MINDIE_DSH_DATA_HOME = path.join(root, 'data')
process.env.MINDIE_DSH_CONFIG_HOME = path.join(root, 'config')
process.env.MINDIE_DSH_STATE_DIR = path.join(root, 'state')
const tools = new Map()
const listeners = new Map()
const disposers = []
let modelCalls = 0
const ctx = {
  tools: { register(tool) { tools.set(tool.name, tool) } },
  agents: {},
  llm: { async *stream() { modelCalls++; throw new Error('unexpected model call') } },
  on(event, callback) { listeners.set(event, callback) },
  effect(callback) { disposers.push(callback()) },
}
const user = text => ({ source: { kind: 'user' }, content: [{ type: 'text', text }] })
try {
  assert.equal(hasMindieGesture([user('/mindie-agent tune KDA')]), true)
  assert.equal(hasMindieGesture([user('What does /mindie-agent do?')]), false)
  assert.equal(hasMindieGesture([user('/mindie-agent'), user('Download a model')]), false)
  assert.equal(hasMindieGesture([{ source: { kind: 'assistant' }, content: [{ type: 'text', text: '/mindie-agent' }] }]), false)
  apply(ctx)
  const exec = { agent: { session: { id: 'ordinary-session', header: { cwd: root } } } }
  const nextDecision = { kind: 'enter', messages: [user('Download a model')] }
  const result = await listeners.get('agent/pre-step')(
    { agent: exec.agent, messages: nextDecision.messages }, async () => nextDecision,
  )
  assert.equal(result, nextDecision)
  for (const [name, args] of [
    ['mindie_entry', { op: 'init' }],
    ['mindie_entry', { op: 'choose', choice: 'contribute' }],
    ['knowledge_query', { query: 'KDA' }],
    ['knowledge_explain', { ref: 'mindie://vllm-ascend/units/kda@1' }],
    ['knowledge_feedback', { ref: 'mindie://vllm-ascend/units/kda@1', rating: 'up' }],
    ['knowledge_sync', {}],
  ]) {
    await assert.rejects(tools.get(name).execute(args, exec), /not activated/)
  }
  assert.equal(modelCalls, 0)
  assert.equal(existsSync(path.join(root, 'state', 'summary-endpoint.json')), false)
  assert.equal(existsSync(path.join(root, 'data', 'admission.sqlite3')), false)
} finally {
  for (const dispose of disposers.reverse()) if (dispose) dispose()
  rmSync(root, { recursive: true, force: true })
}
