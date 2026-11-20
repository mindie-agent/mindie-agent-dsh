// Node contract tests for the DSH path/spawn boundary, host launcher, and generation fiber.
//
// [Role] Verifies canonical `dsh.json` vs legacy config precedence (including UTF-8 BOM),
// Windows windowless spawn options & cold Python resolution, omitted-`sessionId` &
// mid-mount cancellation safety in `host.mjs`, loopback `/summary`, and `Stop` capture.
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
import { pathToFileURL } from 'node:url'

import { apply as applyHost, componentMcpConfig, remoteMcpConfig, resolveSessionId } from '../host.mjs'
import { apply } from '../host3.mjs'

const require = createRequire(import.meta.url)
const pathsModule = require('../paths.cjs')
const { backgroundSpawnOptions, buildChildEnv, paths, resolvePythonCommand } = pathsModule
const KEYS = ['MINDIE_CONFIG_HOME', 'MINDIE_DATA_HOME', 'MINDIE_DSH_CONFIG_HOME', 'MINDIE_DSH_STATE_DIR',
  'MINDIE_DSH_CONFIG', 'MINDIE_AGENT_CONFIG', 'DSH_HOME', 'MINDIE_DSH_UPDATE_DELAY_MS', 'MINDIE_DSH_PYTHON']

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
  delete process.env.MINDIE_DSH_PYTHON
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

test('cold Python launcher prefers uv, rejects WindowsApps stubs, and never returns bare python on win32', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-py-'))
  try {
    const uvDir = path.join(root, 'uv-bin')
    const winApps = path.join(root, 'Microsoft', 'WindowsApps')
    const realPyDir = path.join(root, 'Python313')
    mkdirSync(uvDir, { recursive: true })
    mkdirSync(winApps, { recursive: true })
    mkdirSync(realPyDir, { recursive: true })
    const uvExe = path.join(uvDir, 'uv.exe')
    const stubPy = path.join(winApps, 'python.exe')
    const realPy = path.join(realPyDir, 'python.exe')
    writeFileSync(uvExe, 'MZ-fake-uv')
    writeFileSync(stubPy, '')
    writeFileSync(realPy, 'MZ-real-python')
    const stubBinding = { venvPython: stubPy, configFile: path.join(root, 'dsh.json'), configHome: root, stateDir: path.join(root, 'state') }

    const withUv = resolvePythonCommand(stubBinding, ['launch.py', 'mcp', 'tools'], { PATH: `${winApps};${uvDir};${realPyDir}`, MINDIE_DSH_PYTHON: stubPy }, 'win32')
    assert.equal(withUv.command, uvExe)
    assert.deepEqual(withUv.args, ['run', '--no-project', '--python', '>=3.11,<3.15', 'python', '-B', '-X', 'utf8', 'launch.py', 'mcp', 'tools'])

    const withoutUv = resolvePythonCommand(stubBinding, ['launch.py', 'mcp', 'remote'], { PATH: `${winApps};${realPyDir}`, MINDIE_DSH_PYTHON: 'python' }, 'win32')
    assert.equal(withoutUv.command, realPy)
    assert.deepEqual(withoutUv.args, ['-I', '-X', 'utf8', 'launch.py', 'mcp', 'remote'])

    assert.throws(
      () => resolvePythonCommand(stubBinding, ['launch.py'], { PATH: winApps, MINDIE_DSH_PYTHON: stubPy }, 'win32'),
      /WindowsApps stubs excluded/,
    )

    const entry = path.join(root, 'host3.mjs')
    const mcpCfg = componentMcpConfig(stubBinding, entry, {}, { PATH: `${winApps};${uvDir}` }, 'win32')
    assert.equal(mcpCfg.command, uvExe)
    assert.equal(mcpCfg.args[0], 'run')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('host.mjs attaches omitted-sessionId agents and guards mid-mount disposal races', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-host-'))
  const restore = isolate(root)
  const effectDisposers = []
  try {
    writeFileSync(path.join(root, 'config', 'dsh.json'), '\uFEFF{}')
    const mcpStub = path.join(root, 'mock-mcp-plugin.mjs')
    writeFileSync(
      mcpStub,
      'export const attached = []; export const disposed = []; export let delayMs = 0;\n'
      + 'export function setDelay(ms) { delayMs = ms; }\n'
      + 'export default { name: "mock-mcp", async apply(_ctx, cfg) {\n'
      + '  const tag = cfg.serverName + ":" + (cfg.env.REMOTE_DEV_SESSION_ID || cfg.env.DSH_SESSION_ID) + "#" + attached.length;\n'
      + '  attached.push(cfg);\n'
      + '  if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));\n'
      + '  return { dispose: async () => { disposed.push(tag); } };\n'
      + '} };\n',
    )
    const mcpMod = await import(pathToFileURL(mcpStub).href)
    const makeAgent = (extra = {}) => ({
      ...extra,
      ctx: {
        async plugin(mod, cfg) {
          const impl = mod.default || mod
          return impl.apply(this, cfg)
        },
      },
    })
    const headlessAgent = makeAgent()
    const idOnlyAgent = makeAgent({ id: 'subagent-7', session: { id: '' } })
    const hostListeners = new Map()
    const rootCtx = {
      agents: { list: () => [headlessAgent, idOnlyAgent] },
      llm: { async *stream() {} },
      on(event, cb) { hostListeners.set(event, cb) },
      effect(cb) { effectDisposers.push(cb()) },
      async plugin(p, cfg) {
        const impl = p.default || p
        return impl.apply(rootCtx, cfg)
      },
    }
    await applyHost(rootCtx, { mcpPluginUrl: pathToFileURL(mcpStub).href })
    assert.equal(mcpMod.attached.length, 4)
    const headlessSid = resolveSessionId(headlessAgent)
    assert.match(headlessSid, /^dsh-conn-/)

    // Mid-mount disposal + immediate replacement with same session ID must dispose the late fiber and keep the new mount
    mcpMod.setDelay(40)
    const firstRaceAgent = makeAgent({ id: 'race-sid' })
    const secondRaceAgent = makeAgent({ id: 'race-sid' })
    hostListeners.get('agent/created')({ agent: firstRaceAgent })
    await new Promise((resolve) => setTimeout(resolve, 10))
    hostListeners.get('agent/disposed')({ agent: firstRaceAgent })
    await new Promise((resolve) => setTimeout(resolve, 5))
    mcpMod.setDelay(0)
    hostListeners.get('agent/created')({ agent: secondRaceAgent })
    await new Promise((resolve) => setTimeout(resolve, 60))
    assert.ok(mcpMod.disposed.some((tag) => tag.startsWith('remote-dev:race-sid#')))
    const raceAttachments = mcpMod.attached.filter((c) => (c.env.REMOTE_DEV_SESSION_ID || c.env.DSH_SESSION_ID) === 'race-sid')
    assert.equal(raceAttachments.length, 3)

    const remoteCfg = remoteMcpConfig(paths(), path.join(root, 'host3.mjs'), {})
    assert.equal(remoteCfg.serverName, 'remote-dev')
    assert.match(remoteCfg.env.REMOTE_DEV_SESSION_ID, /^dsh-conn-/)
  } finally {
    for (const close of effectDisposers.reverse()) await close?.()
    restore()
    rmSync(root, { recursive: true, force: true })
  }
})

test('canonical dsh.json wins over legacy and explicit configuration and strips UTF-8 BOM', () => {
  const root = realpathSync.native(mkdtempSync(path.join(tmpdir(), 'mindie-dsh-paths-')))
  const restore = isolate(root)
  try {
    delete process.env.MINDIE_DSH_CONFIG
    const canonical = path.join(root, 'config-base', 'dsh.json')
    const legacy = path.join(root, 'config-base', 'dsh', 'config.json')
    mkdirSync(path.dirname(canonical), { recursive: true })
    writeFileSync(canonical, '\uFEFF{}')
    assert.equal(paths().configFile, realpathSync.native(canonical))
    assert.equal(paths().configHome, path.dirname(realpathSync.native(canonical)))
    rmSync(canonical)
    delete process.env.MINDIE_DSH_CONFIG_HOME
    mkdirSync(path.dirname(legacy), { recursive: true })
    writeFileSync(legacy, '{}')
    assert.equal(paths().configFile, realpathSync.native(legacy))
    rmSync(legacy)
    const explicitHome = path.join(root, 'legacy-home')
    mkdirSync(explicitHome)
    writeFileSync(path.join(explicitHome, 'config.json'), '{}')
    process.env.MINDIE_DSH_CONFIG_HOME = explicitHome
    assert.equal(paths().configFile, realpathSync.native(path.join(explicitHome, 'config.json')))
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

test('capture and idle update spawns are windowless, consent-first, and skip omitted turn ids', async () => {
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
    listeners.get('agent/turn-stopping')({ agent })
    listeners.get('agent/turn-stopping')({ agent, turn: '   ' })
    assert.equal(seen.filter((options) => Array.isArray(options.stdio)).length, 0)
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
