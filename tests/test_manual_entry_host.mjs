import assert from 'node:assert/strict'
import { ChildProcess } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { apply } from '../host3.mjs'

const root = mkdtempSync(path.join(tmpdir(), 'mindie-default-session-'))
const outside = mkdtempSync(path.join(tmpdir(), 'mindie-outside-session-'))
process.env.MINDIE_DSH_DATA_HOME = path.join(root, 'data')
process.env.MINDIE_DSH_CONFIG_HOME = path.join(root, 'config')
process.env.MINDIE_DSH_STATE_DIR = path.join(root, 'state')
process.env.DSH_HOME = path.join(root, 'dsh-home')
const tools = new Map()
const listeners = new Map()
const disposers = []
let modelCalls = 0
const ctx = {
  tools: {
    register(tool) {
      tools.set(tool.name, tool)
      return () => tools.delete(tool.name)
    },
  },
  agents: { list: () => [] },
  llm: { async *stream() { modelCalls++; throw new Error('unexpected model call') } },
  on(event, callback) { listeners.set(event, callback) },
  effect(callback) { disposers.push(callback()) },
}
const user = text => ({ source: { kind: 'user' }, content: [{ type: 'text', text }] })
const originalSpawn = ChildProcess.prototype.spawn
const spawnedOptions = []
const expectedDetached = process.platform !== 'win32'
try {
  apply(ctx)
  // Default-enabled: mindie_entry and all 5 knowledge_* tools are registered immediately on startup.
  assert.deepEqual(
    [...tools.keys()].sort(),
    ['knowledge_distill', 'knowledge_explain', 'knowledge_feedback', 'knowledge_query', 'knowledge_sync', 'mindie_entry'],
  )
  for (const [, def] of tools) {
    assert.equal(String(def.description || '').includes('/mindie-agent'), false)
  }

  // Ordinary session with sharing unconfigured: zero sidecar spawn, zero SQLite, zero Stop spawn.
  const exec = { agent: { session: { id: 'ordinary-session', header: { cwd: root } } } }
  const nextDecision = { kind: 'enter', messages: [user('Download a model')] }
  const result = await listeners.get('agent/pre-step')(
    { agent: exec.agent, messages: nextDecision.messages }, async () => nextDecision,
  )
  assert.equal(result, nextDecision)
  listeners.get('agent/turn-stopping')({ agent: exec.agent, turn: 1 })
  assert.equal(modelCalls, 0)
  assert.equal(spawnedOptions.length, 0)
  assert.equal(existsSync(path.join(root, 'state', 'summary-endpoint.json')), false)
  assert.equal(existsSync(path.join(root, 'data', 'admission.sqlite3')), false)

  mkdirSync(path.join(root, 'config'), { recursive: true })
  mkdirSync(path.join(root, 'data', 'update'), { recursive: true })
  const transcriptDir = path.join(root, 'dsh-home', 'sessions', 'proj', 'opted-in-session')
  mkdirSync(transcriptDir, { recursive: true })
  writeFileSync(path.join(transcriptDir, 'session.v4.jsonl.zstd'), '')
  writeFileSync(path.join(root, 'data', 'update', 'updater.py'), '# stub\n')
  writeFileSync(path.join(root, 'config', 'config.json'), JSON.stringify({
    python: process.execPath,
    venv_dir: path.join(root, 'venv'),
  }))
  writeFileSync(path.join(root, 'config', 'mindie-consent.json'), JSON.stringify({
    schema: 'mindie-consent/1',
    choice: 'contribute',
    choice_revision: 2,
  }))
  writeFileSync(path.join(root, 'config', 'mindie-community.json'), JSON.stringify({
    enabled: true,
    project_roots: [root],
    consent_config: path.join(root, 'config', 'mindie-consent.json'),
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
        'process.stdin.on("data", line => { try { const msg = JSON.parse(String(line)); process.stdout.write(JSON.stringify({ id: msg.id, ok: true, result: { results: [] } }) + "\\n"); } catch {} }); setTimeout(() => {}, 50);',
      ],
    })
  }
  const updateReply = JSON.parse(await tools.get('mindie_entry').execute({ op: 'update-check' }, exec))
  assert.equal(updateReply.started, true)

  // Out-of-scope session does NOT spawn capture_stop.py
  listeners.get('agent/turn-stopping')({
    agent: { session: { id: 'opted-in-session', header: { cwd: outside } } },
    turn: 1,
  })
  assert.equal(spawnedOptions.length, 1)

  // Default-enabled knowledge_query and opted-in in-scope Stop both work without prior activation
  const optedInExec = { agent: { session: { id: 'opted-in-session', header: { cwd: root } } } }
  const queryReply = JSON.parse(await tools.get('knowledge_query').execute({ query: 'Ascend' }, optedInExec))
  assert.deepEqual(queryReply, { results: [] })
  listeners.get('agent/turn-stopping')({ agent: optedInExec.agent, turn: 2 })

  assert.equal(spawnedOptions.length, 3)
  assert.ok(spawnedOptions[0].args.some(arg => String(arg).endsWith('updater.py')))
  assert.equal(spawnedOptions[0].detached, expectedDetached)
  assert.equal(spawnedOptions[0].windowsHide, true)
  assert.ok(spawnedOptions[1].args.some(arg => String(arg).endsWith('mindie_sidecar.py')))
  assert.equal(spawnedOptions[1].windowsHide, true)
  assert.ok(spawnedOptions[2].args.some(arg => String(arg).endsWith('capture_stop.py')))
  assert.equal(spawnedOptions[2].detached, expectedDetached)
  assert.equal(spawnedOptions[2].windowsHide, true)
} finally {
  ChildProcess.prototype.spawn = originalSpawn
  for (const dispose of disposers.reverse()) if (dispose) dispose()
  rmSync(root, { recursive: true, force: true })
  rmSync(outside, { recursive: true, force: true })
}
