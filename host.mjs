// Stable Host launcher for mindie-agent-dsh.
//
// [Role] Mounts the committed `host3.mjs` generation and hot-swaps it when
// `current.json` changes, without restarting DSH.
// [Invariants] A missing/corrupt generation pointer falls back to the bundled
// seed and never leaves the previous fiber unmounted.
// [Timeouts & Locks] One 5s pointer poll; no network/model/child work.
// [Side-Effect Policy] Reads `current.json`; mounts/disposes Cordis fibers and
// writes only the launcher log.
//
// The profile points at this file. The updater writes an immutable generation
// pointer; this launcher mounts the generation as a child Cordis plugin and
// swaps it when the pointer changes, so an update does not require replacing
// the loaded bundle or restarting DSH. The bundled host3.mjs is the seed.

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
    async apply(child) {
      await child.plugin(plugin)
      await child.plugin(remote, {
        serverName: 'remote-dev', transport: 'stdio', command: binding.venvPython,
        args: ['-m', 'remote_dev.mcp.server'], cwd: path.dirname(entry),
        env: {
          MINDIE_DSH_CONFIG: binding.configFile,
          MINDIE_DSH_STATE_DIR: binding.stateDir,
          REMOTE_DEV_STATE_DIR: path.join(binding.stateDir, 'remote'),
          MINDIE_DIAGNOSTICS_CONFIG: path.join(binding.configHome, 'diagnostics.json'),
          // Trusted launcher binding; never derived from tool arguments.
          REMOTE_DEV_HARNESS_PROFILE: 'dsh',
          // DSH exposes a real session id only inside a tool execution
          // (`exec.agent.session.id`), after this single long-lived stdio
          // process has spawned. No per-session id is available here, so keep
          // the install-scoped value rather than fabricating one.
          REMOTE_DEV_SESSION_ID: 'dsh',
        },
        toolCallTimeoutMs: 600000, failOnStartupError: true,
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
