// Node contract tests for the DSH path/spawn boundary and generation fiber.
//
// [Role] Verifies canonical `dsh.json` vs legacy config precedence, Windows
// windowless spawn options, the loopback summary endpoint, and consent-first
// capture/update spawn flags.
// [Invariants] Each test owns one temporary tree and restores every environment
// variable it changes.
// [Timeouts & Locks] Local in-process HTTP and timer waits only; no network.
// [Side-Effect Policy] Writes only inside the per-test temporary tree.
import test from 'node:test'
import assert from 'node:assert/strict'
import { ChildProcess } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { apply } from '../host3.mjs'

const require = createRequire(import.meta.url)
const pathsModule = require('../paths.cjs')
const { backgroundSpawnOptions, buildChildEnv, paths } = pathsModule
const KEYS = ['MINDIE_CONFIG_HOME', 'MINDIE_DATA_HOME', 'MINDIE_DSH_CONFIG_HOME', 'MINDIE_DSH_STATE_DIR',
  'MINDIE_DSH_CONFIG', 'MINDIE_AGENT_CONFIG', 'DSH_HOME', 'MINDIE_DSH_UPDATE_DELAY_MS']

function isolate(root) {
  const previous = Object.fromEntries(KEYS.map((key) => [key, process.env[key]]))
  process.env.MINDIE_CONFIG_HOME = path.join(root, 'config-base')
  process.env.MINDIE_DATA_HOME = path.join(root, 'data')
  process.env.MINDIE_DSH_CONFIG_HOME = path.join(root, 'config')
  process.env.MINDIE_DSH_STATE_DIR = path.join(root, 'state')
  process.env.MINDIE_DSH_CONFIG = path.join(root, 'config', 'dsh.json')
  process.env.DSH_HOME = path.join(root, 'dsh-home')
  process.env.MINDIE_DSH_UPDATE_DELAY_MS = '600000'
  delete process.env.MINDIE_AGENT_CONFIG
  mkdirSync(path.join(root, 'config'), { recursive: true })
  mkdirSync(path.join(root, 'state'), { recursive: true })
  mkdirSync(path.join(root, 'dsh-home'), { recursive: true })
  return () => {
    for (const key of KEYS) {
      if (previous[key] === undefined) delete process.env[key]
      else process.env[key] = previous[key]
    }
  }
}

function makeContext({ agents = [], llmText = '{"blocks":[],"navigation":{"title":"T","summary":"S"}}' } = {}) {
  const listeners = new Map()
  const disposers = []
  const ctx = {
    agents: { list: () => agents },
    llm: {
      async *stream() {
        yield { type: 'text-delta', index: 0, text: llmText }
        yield { type: 'usage', usage: { inputTokens: 7, outputTokens: 3, cacheReadTokens: 1 } }
        yield { type: 'finish', reason: { kind: 'stop' } }
      },
    },
    on(event, callback) { listeners.set(event, callback) },
    effect(callback) { disposers.push(callback()) },
  }
  return { ctx, listeners, dispose: () => { for (const close of disposers.reverse()) close?.() } }
}

async function endpointFrom(root) {
  const file = path.join(root, 'state', 'summary-endpoint.json')
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { return JSON.parse(readFileSync(file, 'utf8')) } catch { await new Promise((resolve) => setTimeout(resolve, 10)) }
  }
  throw new Error('summary endpoint was not written')
}

test('windowless spawn options and Windows PATH normalization', () => {
  assert.deepEqual(backgroundSpawnOptions('win32'), { windowsHide: true, detached: false })
  assert.deepEqual(backgroundSpawnOptions('darwin'), { windowsHide: true, detached: true })
  const env = buildChildEnv({ Path: 'C:\\Old', SystemRoot: 'C:\\Windows' }, { path: 'C:\\Venv;' }, 'win32')
  assert.deepEqual(Object.keys(env).filter((key) => key.toUpperCase() === 'PATH'), ['PATH'])
  assert.equal(env.PATH, 'C:\\Venv;')
  assert.equal(env.PYTHONUTF8, '1')
  assert.equal(env.PYTHONIOENCODING, 'utf-8')
})

test('canonical dsh.json wins over legacy and explicit configuration', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-paths-'))
  const restore = isolate(root)
  try {
    delete process.env.MINDIE_DSH_CONFIG
    const canonical = path.join(root, 'config-base', 'dsh.json')
    const legacy = path.join(root, 'config-base', 'dsh', 'config.json')
    mkdirSync(path.dirname(canonical), { recursive: true })
    writeFileSync(canonical, '{}')
    assert.equal(paths().configFile, realpathSync(canonical))
    assert.equal(paths().configHome, path.dirname(realpathSync(canonical)))
    rmSync(canonical)
    delete process.env.MINDIE_DSH_CONFIG_HOME
    mkdirSync(path.dirname(legacy), { recursive: true })
    writeFileSync(legacy, '{}')
    assert.equal(paths().configFile, realpathSync(legacy))
    rmSync(legacy)
    const explicitHome = path.join(root, 'legacy-home')
    mkdirSync(explicitHome)
    writeFileSync(path.join(explicitHome, 'config.json'), '{}')
    process.env.MINDIE_DSH_CONFIG_HOME = explicitHome
    assert.equal(paths().configFile, realpathSync(path.join(explicitHome, 'config.json')))
    mkdirSync(path.dirname(canonical), { recursive: true })
    writeFileSync(canonical, '{broken')
    assert.throws(() => paths(), /JSON/)
  } finally {
    restore()
    rmSync(root, { recursive: true, force: true })
  }
})

test('summary endpoint enforces bearer auth, schema dispatch, and the 512 KiB cap', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-summary-'))
  const restore = isolate(root)
  const { ctx, listeners, dispose } = makeContext()
  try {
    writeFileSync(path.join(root, 'config', 'dsh.json'), '{}')
    apply(ctx)
    await listeners.get('agent/created')({
      agent: { session: { id: 'session-1' }, options: { provider: 'p', model: 'm' } },
    })
    const endpoint = await endpointFrom(root)
    assert.match(endpoint.url, /^http:\/\/127\.0\.0\.1:\d+\/summary$/)
    assert.deepEqual(endpoint.routes['session-1'], { provider: 'p', model: 'm' })
    const request = {
      schema: 'mindie-summary-request/1', prompt: 'index',
      request: { task_id: 'a'.repeat(64), blocks: [{ block_id: 'b'.repeat(64) }], policy_identity: { model: 'm' } },
    }
    const ok = await fetch(endpoint.url, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${endpoint.token}` }, body: JSON.stringify(request),
    })
    assert.equal(ok.status, 200)
    const body = await ok.json()
    assert.match(body.text, /"navigation"/)
    assert.deepEqual(body.usage, { input_tokens: 7, cached_input_tokens: 1, output_tokens: 3 })
    const denied = await fetch(endpoint.url, { method: 'POST', body: JSON.stringify(request) })
    assert.equal(denied.status, 403)
    const distill = await fetch(endpoint.url, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${endpoint.token}` },
      body: JSON.stringify({
        schema: 'mindie-distill-request/1', prompt: 'distill', output_schema: { type: 'object' },
        request: { policy_identity: { model: 'm', implementation: { provider: 'p' } } },
      }),
    })
    assert.equal(distill.status, 200)
    const changed = await fetch(endpoint.url, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${endpoint.token}` },
      body: JSON.stringify({
        schema: 'mindie-distill-request/1', prompt: 'distill', output_schema: { type: 'object' },
        request: { policy_identity: { model: 'changed', implementation: { provider: 'p' } } },
      }),
    })
    assert.equal(changed.status, 409)
    let capStatus = 0
    try {
      const oversized = await fetch(endpoint.url, {
        method: 'POST', headers: { authorization: `Bearer ${endpoint.token}` }, body: 'x'.repeat(513 * 1024),
      })
      capStatus = oversized.status
    } catch { capStatus = 413 }
    assert.equal(capStatus, 413)
  } finally {
    dispose()
    restore()
    rmSync(root, { recursive: true, force: true })
  }
})

test('capture and idle update spawns are windowless and consent-first', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-capture-'))
  const restore = isolate(root)
  const originalSpawn = ChildProcess.prototype.spawn
  const seen = []
  ChildProcess.prototype.spawn = function (options) {
    seen.push(options)
    return originalSpawn.call(this, { ...options, file: process.execPath, args: ['-e', 'setTimeout(() => {}, 400)'] })
  }
  const { ctx, listeners, dispose } = makeContext({ agents: [] })
  try {
    writeFileSync(path.join(root, 'config', 'dsh.json'), JSON.stringify({}))
    writeFileSync(path.join(root, 'config', 'mindie-community.json'), JSON.stringify({ enabled: true, project_roots: [root] }))
    writeFileSync(path.join(root, 'config', 'mindie-consent.json'), JSON.stringify({ choice: 'local-only' }))
    const transcript = path.join(root, 'dsh-home', 'sessions', 'project', 'session-1', 'session.v4.jsonl.zstd')
    mkdirSync(path.dirname(transcript), { recursive: true })
    writeFileSync(transcript, '')
    process.env.MINDIE_DSH_UPDATE_DELAY_MS = '5'
    apply(ctx)
    await new Promise((resolve) => setTimeout(resolve, 50))
    assert.ok(seen.some((options) => options.stdio === 'ignore' && options.windowsHide === true))
    const agent = { session: { id: 'session-1', header: { cwd: root } }, options: { provider: 'p', model: 'm' } }
    listeners.get('agent/turn-stopping')({ agent, turn: 2 })
    const capture = seen.find((options) => Array.isArray(options.stdio))
    assert.ok(capture)
    assert.equal(capture.windowsHide, true)
    assert.equal(capture.detached, process.platform !== 'win32')
    assert.equal(capture.stdio[0], 'pipe')
  } finally {
    ChildProcess.prototype.spawn = originalSpawn
    dispose()
    restore()
    rmSync(root, { recursive: true, force: true })
  }
})
