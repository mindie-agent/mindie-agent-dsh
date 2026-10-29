// Stable Host launcher for mindie-agent-dsh.
//
// [Role] Mounts the committed `host3.mjs` generation and hot-swaps it when
// `current.json` changes. The `host.mjs` launcher itself is loaded by DSH at
// startup and is NOT hot-replaced; a native package update therefore records
// needs_host_reload=true and only a natural host reload/new process activates
// launcher changes. Existing sessions keep the loaded generation.
// [Invariants] A missing/corrupt generation pointer falls back to the bundled
// seed and never leaves the previous fiber unmounted.
// [Timeouts & Locks] One 5s pointer poll; component calls allow 600s plus
// cleanup inside the 660s MCP client deadline. No model calls.
// [Side-Effect Policy] Reads `current.json`; mounts/disposes Cordis fibers and
// writes only the launcher log. Remote MCP toolCallTimeoutMs is derived from
// the 600000 ms native Bash maximum plus a 60000 ms SSH/cleanup grace, and is
// configurable above that floor.
//
// The profile points at this file. The updater writes an immutable generation
// pointer; this launcher mounts the generation as a child Cordis plugin and
// swaps the child generation when the pointer changes. A host3-only change
// does not restart DSH; changing this launcher or native profile wiring needs
// the natural host reload boundary above. The bundled host3.mjs is the seed.

import { createRequire } from 'node:module'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const require = createRequire(import.meta.url)
const paths = require('./paths.cjs').paths()
const seedProfileMatch = String(import.meta.url).match(/[/\\]profiles[/\\]([^/\\]+)[/\\]node_modules[/\\]/)
if (seedProfileMatch && !process.env.MINDIE_DSH_PROFILE) {
  process.env.MINDIE_DSH_PROFILE = seedProfileMatch[1]
}
const logPath = path.join(paths.dataHome, 'update', 'launcher.log')
const log = (message) => { try { appendFileSync(logPath, new Date().toISOString() + ' ' + message + '\n') } catch {} }

export const name = 'mindie-agent-dsh'
export const inject = ['tools', 'agents', 'llm']

// DSH's native Bash timeoutMs maximum is 600000. The MCP client timeout must
// exceed that inner wait plus SSH setup, MCP tools/call framing, JSON drain and
// child cleanup; equality would cut the call off exactly when it is still legal.
export const NATIVE_TOOL_CALL_MAX_MS = 600000
export const REMOTE_CALL_GRACE_MS = 60000

export function remoteToolCallTimeoutMs(env = process.env) {
  const native = Number(env?.MINDIE_DSH_NATIVE_TOOL_CALL_MAX_MS ?? NATIVE_TOOL_CALL_MAX_MS)
  const grace = Number(env?.MINDIE_DSH_REMOTE_CALL_GRACE_MS ?? REMOTE_CALL_GRACE_MS)
  const derived = (Number.isFinite(native) && native > 0 ? native : NATIVE_TOOL_CALL_MAX_MS)
    + (Number.isFinite(grace) && grace > 0 ? grace : REMOTE_CALL_GRACE_MS)
  const requested = Number(env?.MINDIE_DSH_REMOTE_TOOL_CALL_TIMEOUT_MS)
  return Number.isFinite(requested) && requested > derived ? requested : derived
}

export function componentMcpConfig(binding, entry, agent) {
  return {
    serverName: 'mindie-tools', transport: 'stdio', command: binding.venvPython,
    args: [path.join(path.dirname(entry), 'scripts', 'component_mcp.py')],
    cwd: agent?.session?.header?.cwd || path.dirname(entry),
    env: {
      MINDIE_DSH_CONFIG: binding.configFile,
      MINDIE_DSH_STATE_DIR: binding.stateDir,
      MINDIE_DSH_SESSION_ID: agent.session.id,
      MINDIE_DIAGNOSTICS_CONFIG: path.join(binding.configHome, 'diagnostics.json'),
    },
    toolCallTimeoutMs: NATIVE_TOOL_CALL_MAX_MS + REMOTE_CALL_GRACE_MS,
    failOnStartupError: true,
  }
}

function sessionStateDir(binding, sessionId) {
  // One MCP process and one state root per agent session. A shared root would
  // let session B read or stop session A's job by opaque job_id.
  const safe = String(sessionId).replace(/[^A-Za-z0-9._-]/g, '_')
  if (!safe || safe === '.' || safe === '..') {
    throw new Error('MindIE remote MCP requires a filesystem-safe agent session id')
  }
  return path.join(binding.stateDir, 'remote', safe)
}

function pointerPath() {
  return path.join(paths.dataHome, 'update', 'current.json')
}

function entryFor() {
  try {
    const installed = JSON.parse(readFileSync(paths.configFile, 'utf8'))
    if (typeof installed.package_dir === 'string') {
      const candidate = path.join(installed.package_dir, 'host3.mjs')
      if (existsSync(candidate)) return candidate
      throw new Error('published adapter host entry is missing: ' + candidate)
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error
  }
  try {
    const pointer = JSON.parse(readFileSync(pointerPath(), 'utf8'))
    if (typeof pointer?.generation === 'string' && pointer.generation) {
      const candidate = path.join(pointer.generation, 'host3.mjs')
      if (existsSync(candidate)) return candidate
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      log('generation pointer unreadable: ' + String(error?.message || error))
    }
  }
  return path.join(paths.packageDir, 'host3.mjs')
}

async function mount(ctx, entry, config, binding = require('./paths.cjs').paths()) {
  const module = await import(pathToFileURL(entry).href)
  const plugin = module.default && typeof module.default.apply === 'function' ? module.default : module
  if (!plugin || typeof plugin.apply !== 'function') {
    throw new Error('generation has no apply function: ' + entry)
  }
  const remote = await import(config.mcpPluginUrl)
  const generation = {
    name: 'mindie-agent-generation',
    inject: ['agents'],
    async apply(child) {
      await child.plugin(plugin)
      // One remote-dev stdio process per live agent/session. `agent/created`
      // is serial and awaited before the agent becomes visible; `agent.ctx`
      // owns the scoped registration, so a session id is a real identity rather
      // than an install-wide sentinel. Existing idle agents are attached too so
      // a generation switch does not drop their remote tools.
      const mounted = new Map()
      const attach = async (agent) => {
        const sessionId = agent?.session?.id
        if (typeof sessionId !== 'string' || sessionId.length === 0) {
          throw new Error('MindIE remote MCP requires a non-empty agent session id')
        }
        if (mounted.has(sessionId)) return
        const fiber = await agent.ctx.plugin(remote, {
          serverName: 'remote-dev', transport: 'stdio', command: binding.venvPython,
          args: ['-m', 'remote_dev.mcp.server'], cwd: path.dirname(entry),
          env: {
            MINDIE_DSH_CONFIG: binding.configFile,
            MINDIE_DSH_STATE_DIR: binding.stateDir,
            REMOTE_DEV_STATE_DIR: sessionStateDir(binding, sessionId),
            MINDIE_DIAGNOSTICS_CONFIG: path.join(binding.configHome, 'diagnostics.json'),
            // Trusted launcher binding; never derived from tool arguments.
            REMOTE_DEV_HARNESS_PROFILE: 'dsh',
            // The DSH agent/session identity available at this supported hook.
            REMOTE_DEV_SESSION_ID: sessionId,
            // Clear inherited session names from other harnesses; the core
            // already prefers REMOTE_DEV_SESSION_ID, and empty values keep a
            // stale parent scope out of the child entirely.
            CLAUDE_SESSION_ID: '',
            CLAUDE_CODE_SESSION_ID: '',
            CODEX_SESSION_ID: '',
            CODEX_RUN_ID: '',
            CODEX_THREAD_ID: '',
          },
          toolCallTimeoutMs: remoteToolCallTimeoutMs(), failOnStartupError: true,
        })
        let components
        try {
          components = await agent.ctx.plugin(remote, componentMcpConfig(binding, entry, agent))
        } catch (error) {
          await fiber?.dispose?.()
          throw error
        }
        mounted.set(sessionId, { dispose: async () => {
          try { await components?.dispose?.() } finally { await fiber?.dispose?.() }
        } })
      }
      const detach = async (agent) => {
        const sessionId = agent?.session?.id
        const fiber = mounted.get(sessionId)
        if (!fiber) return
        mounted.delete(sessionId)
        await fiber?.dispose?.()
      }
      for (const agent of child.agents.list()) await attach(agent)
      child.on('agent/created', ({ agent }) => attach(agent))
      child.on('agent/disposed', ({ agent }) => detach(agent))
      child.effect(() => async () => {
        for (const [sessionId, fiber] of mounted) {
          mounted.delete(sessionId)
          await fiber?.dispose?.()
        }
      })
    },
  }
  const fiber = await ctx.plugin(generation)
  log('mounted ' + entry)
  return { fiber, entry, binding }
}

export async function apply(ctx, config) {
  log('launcher start seed=' + entryFor())
  let current = await mount(ctx, entryFor(), config)
  let switching = false
  let timer = setInterval(async () => {
    if (switching) return
    const entry = entryFor()
    if (entry === current.entry) return
    if (ctx.agents.list().some((agent) => agent?.status === 'running')) return
    const previous = current
    switching = true
    try {
      await previous.fiber?.dispose?.()
      current = await mount(ctx, entry, config)
    } catch (error) {
      log('mount failed ' + entry + ' ' + String(error?.message || error))
      try {
        current = await mount(ctx, previous.entry, config, previous.binding)
      } catch (rollbackError) {
        log('rollback failed ' + String(rollbackError?.message || rollbackError))
      }
    } finally {
      switching = false
    }
  }, 5000)
  timer.unref?.()
  ctx.effect(() => () => {
    clearInterval(timer)
    current?.fiber?.dispose?.().catch(() => {})
  }, 'mindie-agent-dsh.launcher')
}

export default { name, inject, apply }
