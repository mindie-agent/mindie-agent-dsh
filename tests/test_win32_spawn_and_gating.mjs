// Orthogonal behavioral tests for Windows zero-popup spawn options, Win32 PATH
// case normalization, and default-enabled `knowledge_*` tool mounting with per-session deactivation.

import test from 'node:test'
import assert from 'node:assert/strict'
import { ChildProcess } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { apply } from '../host3.mjs'

const require = createRequire(import.meta.url)
const { backgroundSpawnOptions, buildChildEnv } = require('../paths.cjs')

test('backgroundSpawnOptions never sets detached:true on win32 and always hides windows', () => {
  assert.deepEqual(backgroundSpawnOptions('win32'), { windowsHide: true, detached: false })
  assert.deepEqual(backgroundSpawnOptions('darwin'), { windowsHide: true, detached: true })
  assert.deepEqual(backgroundSpawnOptions('linux'), { windowsHide: true, detached: true })
})

test('buildChildEnv normalizes mixed-case Path/PATH keys on win32 into a single PATH key', () => {
  const env = buildChildEnv(
    { Path: 'C:\\Windows\\System32;C:\\Old', SystemRoot: 'C:\\Windows' },
    { path: 'C:\\Venv\\Scripts;C:\\Windows\\System32' },
    'win32',
  )
  const pathKeys = Object.keys(env).filter((key) => key.toUpperCase() === 'PATH')
  assert.deepEqual(pathKeys, ['PATH'])
  assert.equal(env.PATH, 'C:\\Venv\\Scripts;C:\\Windows\\System32')
  assert.equal(env.PYTHONUTF8, '1')
  assert.equal(env.PYTHONIOENCODING, 'utf-8')
  assert.equal(env.SystemRoot, 'C:\\Windows')
})

test('host3 exposes knowledge_* tools by default and scopes deactivate/dispose per session', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-gating-'))
  const oldData = process.env.MINDIE_DSH_DATA_HOME
  const oldConfig = process.env.MINDIE_DSH_CONFIG_HOME
  const oldState = process.env.MINDIE_DSH_STATE_DIR
  const oldDsh = process.env.DSH_HOME
  const originalSpawn = ChildProcess.prototype.spawn
  try {
    process.env.MINDIE_DSH_DATA_HOME = path.join(root, 'data')
    process.env.MINDIE_DSH_CONFIG_HOME = path.join(root, 'config')
    process.env.MINDIE_DSH_STATE_DIR = path.join(root, 'state')
    process.env.DSH_HOME = path.join(root, 'dsh-home')
    mkdirSync(path.join(root, 'config'), { recursive: true })
    mkdirSync(path.join(root, 'data', 'update'), { recursive: true })
    writeFileSync(path.join(root, 'config', 'config.json'), JSON.stringify({
      python: process.execPath,
      venv_dir: path.join(root, 'venv'),
    }))

    ChildProcess.prototype.spawn = function (options) {
      return originalSpawn.call(this, {
        ...options,
        file: process.execPath,
        args: [
          process.execPath,
          '-e',
          `process.stdin.on("data", line => {
            try {
              const msg = JSON.parse(String(line));
              const res = msg.op === "deactivate"
                ? { deactivated: true, status: "inactive", sharing: { enabled: false }, domains: [] }
                : msg.op === "rpc"
                ? { results: [] }
                : { session: msg.args?.session || "s", token: "tok-" + (msg.args?.session || "s"), activated_at: 1, project_root: process.cwd(), sharing: { enabled: false } };
              process.stdout.write(JSON.stringify({ id: msg.id, ok: true, result: res }) + "\\n");
            } catch {}
          });
          setTimeout(() => {}, 100);`,
        ],
      })
    }

    const makeRegistry = () => {
      const map = new Map()
      return {
        map,
        register(def) {
          map.set(def.name, def)
          return () => map.delete(def.name)
        },
      }
    }

    const globalTools = makeRegistry()
    const agentATools = makeRegistry()
    const agentBTools = makeRegistry()
    const listeners = new Map()
    const disposers = []
    const ctx = {
      tools: globalTools,
      agents: { list: () => [] },
      llm: { async *stream() { throw new Error('unexpected llm stream') } },
      on(event, cb) { listeners.set(event, cb) },
      effect(cb) { disposers.push(cb()) },
    }

    apply(ctx)
    assert.deepEqual(
      [...globalTools.map.keys()].sort(),
      ['knowledge_distill', 'knowledge_explain', 'knowledge_feedback', 'knowledge_query', 'knowledge_sync', 'mindie_entry'],
    )

    const agentA = { session: { id: 'sess-A', header: { cwd: root } }, ctx: { tools: agentATools } }
    const agentB = { session: { id: 'sess-B', header: { cwd: root } }, ctx: { tools: agentBTools } }
    const userMsg = (text) => ({ source: { kind: 'user' }, content: [{ type: 'text', text }] })

    // Ordinary turns mount knowledge_* tools on both sessions without any slash command
    await listeners.get('agent/pre-step')(
      { agent: agentA, messages: [userMsg('optimize operator')] },
      async () => ({ kind: 'enter', messages: [userMsg('optimize operator')] }),
    )
    await listeners.get('agent/pre-step')(
      { agent: agentB, messages: [userMsg('run pytest')] },
      async () => ({ kind: 'enter', messages: [userMsg('run pytest')] }),
    )
    assert.equal(agentATools.map.size, 5)
    assert.equal(agentBTools.map.size, 5)

    // Deactivating sess-A unmounts agentATools and blocks sess-A calls while keeping sess-B active
    const deact = JSON.parse(await globalTools.map.get('mindie_entry').execute(
      { op: 'deactivate' },
      { agent: agentA, signal: undefined },
    ))
    assert.equal(deact.deactivated, true)
    assert.equal(agentATools.map.size, 0)
    await assert.rejects(
      globalTools.map.get('knowledge_query').execute({ query: 'Ascend' }, { agent: agentA, signal: undefined }),
      /not activated/,
    )
    const okB = JSON.parse(await globalTools.map.get('knowledge_query').execute(
      { query: 'Ascend' },
      { agent: agentB, signal: undefined },
    ))
    assert.deepEqual(okB, { results: [] })

    listeners.get('agent/disposed')({ agent: agentB })
    assert.equal(agentBTools.map.size, 0)

    for (const d of disposers.reverse()) d?.()
  } finally {
    ChildProcess.prototype.spawn = originalSpawn
    if (oldData === undefined) delete process.env.MINDIE_DSH_DATA_HOME
    else process.env.MINDIE_DSH_DATA_HOME = oldData
    if (oldConfig === undefined) delete process.env.MINDIE_DSH_CONFIG_HOME
    else process.env.MINDIE_DSH_CONFIG_HOME = oldConfig
    if (oldState === undefined) delete process.env.MINDIE_DSH_STATE_DIR
    else process.env.MINDIE_DSH_STATE_DIR = oldState
    if (oldDsh === undefined) delete process.env.DSH_HOME
    else process.env.DSH_HOME = oldDsh
    rmSync(root, { recursive: true, force: true })
  }
})
