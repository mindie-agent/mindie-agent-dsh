// Stable thin Cordis host launcher for mindie-agent-dsh.
// [Role] Mounts `host3.mjs` (bundled or the active package recorded in
// `dsh.json`) and attaches one `remote-dev` plus one `mindie-tools` stdio MCP
// server per live agent session through `@deepseek-ai/dsh-mcp-client`.
// [Invariants] Every child launches the bootstrap-safe Python and
// `scripts/mindie_launch.py`; a failed second attach disposes the first; MCP
// sessions with empty or dot-only ids are rejected before spawning.
// [Timeouts & Locks] Native calls allow 600s plus a 60s SSH/cleanup grace inside
// the 660s MCP client deadline. No model calls, locks, or polling loops.
// [Side-Effect Policy] Read-only config/entry resolution; mounts Cordis child
// plugins and starts two bounded child processes per agent session.
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
const require = createRequire(import.meta.url)
const { paths: runtimePaths, buildChildEnv } = require('./paths.cjs')
export const name = 'mindie-agent-dsh'
export const inject = ['tools', 'agents', 'llm']
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
function pythonFor(binding) { return existsSync(binding.venvPython) ? binding.venvPython : (process.env.MINDIE_DSH_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')) }
function launchArgs(binding, entry, surface) {
  return ['-I', '-X', 'utf8', path.join(path.dirname(entry), 'scripts', 'mindie_launch.py'),
    '--config', binding.configFile, 'mcp', surface]
}
export function componentMcpConfig(binding, entry, agent) {
  const sessionId = agent.session.id
  return {
    serverName: 'mindie-tools', transport: 'stdio', command: pythonFor(binding),
    args: launchArgs(binding, entry, 'tools'),
    cwd: agent?.session?.header?.cwd || path.dirname(entry),
    env: buildChildEnv(process.env, { MINDIE_DSH_CONFIG: binding.configFile, MINDIE_DSH_STATE_DIR: binding.stateDir, MINDIE_DIAGNOSTICS_CONFIG: path.join(binding.configHome, 'diagnostics.json'), DSH_SESSION_ID: sessionId, MINDIE_SESSION_ID: sessionId }),
    toolCallTimeoutMs: NATIVE_TOOL_CALL_MAX_MS + REMOTE_CALL_GRACE_MS,
    failOnStartupError: true,
  }
}
function remoteMcpConfig(binding, entry, agent) {
  const sessionId = agent.session.id
  const safe = String(sessionId).replace(/[^A-Za-z0-9._-]/g, '_')
  if (!safe || safe === '.' || safe === '..') throw new Error('MindIE remote MCP requires a filesystem-safe agent session id')
  return {
    serverName: 'remote-dev', transport: 'stdio', command: pythonFor(binding),
    args: launchArgs(binding, entry, 'remote'),
    cwd: agent?.session?.header?.cwd || path.dirname(entry),
    env: buildChildEnv(process.env, { MINDIE_DSH_CONFIG: binding.configFile, MINDIE_DSH_STATE_DIR: binding.stateDir, REMOTE_DEV_STATE_DIR: path.join(binding.stateDir, 'remote'), REMOTE_DEV_HARNESS_PROFILE: 'dsh', REMOTE_DEV_SESSION_ID: sessionId, CLAUDE_SESSION_ID: '', CLAUDE_CODE_SESSION_ID: '', CODEX_SESSION_ID: '', CODEX_RUN_ID: '', CODEX_THREAD_ID: '' }),
    toolCallTimeoutMs: remoteToolCallTimeoutMs(),
    failOnStartupError: true,
  }
}
function entryFor(binding) {
  let installed = {}
  try { installed = JSON.parse(readFileSync(binding.configFile, 'utf8')) }
  catch (error) { if (error?.code !== 'ENOENT') throw error }
  for (const root of [installed?.source_root, installed?.package_dir]) {
    if (typeof root === 'string' && path.isAbsolute(root) && existsSync(path.join(root, 'host3.mjs'))) return path.join(root, 'host3.mjs')
  }
  return path.join(binding.packageDir, 'host3.mjs')
}
async function mount(ctx, entry, config, binding = runtimePaths()) {
  const plugin = await import(pathToFileURL(entry).href)
  const generation = plugin.default && typeof plugin.default.apply === 'function' ? plugin.default : plugin
  if (!generation || typeof generation.apply !== 'function') throw new Error('generation has no apply function: ' + entry)
  if (typeof config?.mcpPluginUrl !== 'string' || !config.mcpPluginUrl) throw new Error('mindie-agent-host requires the mcpPluginUrl config value')
  const remote = await import(config.mcpPluginUrl)
  const generationPlugin = {
    name: 'mindie-agent-generation', inject: ['agents'],
    async apply(child) {
      await child.plugin(generation)
      const mounted = new Map(), disposed = new Set()
      const attach = async (agent) => {
        const sessionId = agent?.session?.id
        if (typeof sessionId !== 'string' || sessionId.length === 0) throw new Error('MindIE remote MCP requires a non-empty agent session id')
        if (mounted.has(sessionId) || disposed.has(sessionId)) return
        mounted.set(sessionId, { dispose: async () => {} })
        let remoteFiber, tools
        try {
          remoteFiber = await agent.ctx.plugin(remote, remoteMcpConfig(binding, entry, agent))
          tools = await agent.ctx.plugin(remote, componentMcpConfig(binding, entry, agent))
        } catch (error) {
          mounted.delete(sessionId)
          await tools?.dispose?.()
          await remoteFiber?.dispose?.()
          throw error
        }
        const pair = { dispose: async () => { try { await tools?.dispose?.() } finally { await remoteFiber?.dispose?.() } } }
        if (disposed.has(sessionId)) { mounted.delete(sessionId); await pair.dispose(); return }
        mounted.set(sessionId, pair)
      }
      const detach = async (agent) => {
        const sessionId = agent?.session?.id
        if (typeof sessionId === 'string') disposed.add(sessionId)
        const fiber = mounted.get(sessionId); if (!fiber) return; mounted.delete(sessionId); await fiber.dispose()
      }
      child.on('agent/created', ({ agent }) => { disposed.delete(agent?.session?.id); attach(agent).catch(() => {}) })
      child.on('agent/disposed', ({ agent }) => { detach(agent).catch(() => {}) })
      for (const agent of child.agents.list()) await attach(agent)
      child.effect(() => async () => { for (const [sessionId, fiber] of mounted) { mounted.delete(sessionId); await fiber.dispose() } })
    },
  }
  return ctx.plugin(generationPlugin)
}
export async function apply(ctx, config) {
  const binding = runtimePaths()
  await mount(ctx, entryFor(binding), config, binding)
}
export default { name, inject, apply }
