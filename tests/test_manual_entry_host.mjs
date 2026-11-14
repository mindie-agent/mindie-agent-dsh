import assert from 'node:assert/strict'
import { ChildProcess } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { apply } from '../host3.mjs'
import { hasMindieGesture } from '../scripts/knowledge_tools.mjs'

const root = mkdtempSync(path.join(tmpdir(), 'mindie-passive-session-'))
process.env.MINDIE_DSH_DATA_HOME = path.join(root, 'data')
process.env.MINDIE_DSH_CONFIG_HOME = path.join(root, 'config')
process.env.MINDIE_DSH_STATE_DIR = path.join(root, 'state')
process.env.DSH_HOME = path.join(root, 'dsh-home')
const tools = new Map()
const listeners = new Map()
const disposers = []
let modelCalls = 0
const ctx = {
  tools: { register(tool) { tools.set(tool.name, tool) } },
  agents: { list: () => [] },
  llm: { async *stream() { modelCalls++; throw new Error('unexpected model call') } },
  on(event, callback) { listeners.set(event, callback) },
  effect(callback) { disposers.push(callback()) },
}
const user = text => ({ source: { kind: 'user' }, content: [{ type: 'text', text }] })
const originalSpawn = ChildProcess.prototype.spawn
const spawnedOptions = []
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

  mkdirSync(path.join(root, 'config'), { recursive: true })
  mkdirSync(path.join(root, 'data', 'update'), { recursive: true })
  const transcriptDir = path.join(root, 'dsh-home', 'sessions', 'proj', 'activated-session')
  mkdirSync(transcriptDir, { recursive: true })
  writeFileSync(path.join(transcriptDir, 'session.v4.jsonl.zstd'), '')
  writeFileSync(path.join(root, 'data', 'update', 'updater.py'), '# stub\n')
  writeFileSync(path.join(root, 'config', 'config.json'), JSON.stringify({
    python: process.execPath,
    venv_dir: path.join(root, 'venv'),
  }))
  ChildProcess.prototype.spawn = function (options) {
    spawnedOptions.push({
      file: options.file,
      args: options.args,
      detached: Boolean(options.detached),
      windowsHide: Boolean(options.windowsHide),
    })
    return originalSpawn.call(this, {
      ...options,
      file: process.execPath,
      args: [
        process.execPath,
        '-e',
        'process.stdin.on("data", line => { try { const msg = JSON.parse(String(line)); process.stdout.write(JSON.stringify({ id: msg.id, ok: true, result: { session: "activated-session", token: "tok-1", activated_at: 1, project_root: process.cwd(), sharing: { enabled: true } } }) + "\\n"); } catch {} }); setTimeout(() => {}, 50);',
      ],
    })
  }
  const updateReply = JSON.parse(await tools.get('mindie_entry').execute({ op: 'update-check' }, exec))
  assert.equal(updateReply.started, true)
  const activatedExec = { agent: { session: { id: 'activated-session', header: { cwd: root } } } }
  const gestureMessages = [user('/mindie-agent check windowsHide')]
  await listeners.get('agent/pre-step')(
    { agent: activatedExec.agent, messages: gestureMessages },
    async () => ({ kind: 'enter', messages: gestureMessages }),
  )
  listeners.get('agent/turn-stopping')({ agent: activatedExec.agent, turn: 1 })
  assert.equal(spawnedOptions.length, 3)
  assert.ok(spawnedOptions[0].args.some(arg => String(arg).endsWith('updater.py')))
  assert.equal(spawnedOptions[0].detached, true)
  assert.equal(spawnedOptions[0].windowsHide, true)
  assert.ok(spawnedOptions[1].args.some(arg => String(arg).endsWith('mindie_sidecar.py')))
  assert.equal(spawnedOptions[1].windowsHide, true)
  assert.ok(spawnedOptions[2].args.some(arg => String(arg).endsWith('capture_stop.py')))
  assert.equal(spawnedOptions[2].detached, true)
  assert.equal(spawnedOptions[2].windowsHide, true)
} finally {
  ChildProcess.prototype.spawn = originalSpawn
  for (const dispose of disposers.reverse()) if (dispose) dispose()
  rmSync(root, { recursive: true, force: true })
}
