// Stable thin Cordis host launcher for mindie-agent-dsh.
// [Role] Mounts `host3.mjs` (bundled or the active package recorded in
// `dsh.json`) and attaches one `remote-dev` plus one `mindie-tools` stdio MCP
// server per live agent session through `@deepseek-ai/dsh-mcp-client`.
// [Invariants] Resolves each session ID once into a per-mount record with a
// `closed` flag across `await` boundaries; disposes partial/late mounts cleanly.
// [Timeouts & Locks] Native calls allow 600s plus a 60s SSH/cleanup grace inside
// the 660s MCP client deadline. No model calls, locks, or polling loops.
// [Side-Effect Policy] Read-only config/entry resolution; mounts Cordis child
// plugins and starts two bounded child processes per agent session.
import { randomUUID } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
const require = createRequire(import.meta.url)
const { paths: runtimePaths, buildChildEnv, resolvePythonCommand } = require('./paths.cjs')
export const name = 'mindie-agent-dsh'
export const inject = ['tools', 'agents', 'llm']
export const NATIVE_TOOL_CALL_MAX_MS = 600000
export const REMOTE_CALL_GRACE_MS = 60000
const agentSessionIds = new WeakMap()
export function remoteToolCallTimeoutMs(env = process.env) {
  const native = Number(env?.MINDIE_DSH_NATIVE_TOOL_CALL_MAX_MS ?? NATIVE_TOOL_CALL_MAX_MS)
  const grace = Number(env?.MINDIE_DSH_REMOTE_CALL_GRACE_MS ?? REMOTE_CALL_GRACE_MS)
  const derived = (Number.isFinite(native) && native > 0 ? native : NATIVE_TOOL_CALL_MAX_MS)
    + (Number.isFinite(grace) && grace > 0 ? grace : REMOTE_CALL_GRACE_MS)
  const requested = Number(env?.MINDIE_DSH_REMOTE_TOOL_CALL_TIMEOUT_MS)
  return Number.isFinite(requested) && requested > derived ? requested : derived
}
export function resolveSessionId(agent) {
  for (const candidate of [agent?.session?.id, agent?.sessionId, agent?.id]) {
    if (typeof candidate === 'string' && candidate.trim()) {
      const trimmed = candidate.trim()
      const safe = trimmed.replace(/[^A-Za-z0-9._-]/g, '_')
      if (safe && safe !== '.' && safe !== '..') return trimmed
    }
  }
  if (agent && typeof agent === 'object') {
    let fallback = agentSessionIds.get(agent)
    if (!fallback) { fallback = `dsh-conn-${randomUUID()}`; agentSessionIds.set(agent, fallback) }
    return fallback
  }
  return `dsh-conn-${randomUUID()}`
}
function launchTarget(binding, entry, surface, env = process.env, platform = process.platform) {
  return resolvePythonCommand(binding, [
    path.join(path.dirname(entry), 'scripts', 'mindie_launch.py'), '--config', binding.configFile, 'mcp', surface,
  ], env, platform)
}
export function componentMcpConfig(binding, entry, agent, env = process.env, platform = process.platform, sid = undefined) {
  const sessionId = sid || resolveSessionId(agent)
  const { command, args } = launchTarget(binding, entry, 'tools', env, platform)
  return {
    serverName: 'mindie-tools', transport: 'stdio', command, args,
    cwd: agent?.session?.header?.cwd || agent?.cwd || path.dirname(entry),
    env: buildChildEnv(env, { MINDIE_DSH_CONFIG: binding.configFile, MINDIE_DSH_STATE_DIR: binding.stateDir, MINDIE_DIAGNOSTICS_CONFIG: path.join(binding.configHome, 'diagnostics.json'), DSH_SESSION_ID: sessionId, MINDIE_SESSION_ID: sessionId }, platform),
    toolCallTimeoutMs: NATIVE_TOOL_CALL_MAX_MS + REMOTE_CALL_GRACE_MS,
    failOnStartupError: true,
  }
}
export function remoteMcpConfig(binding, entry, agent, env = process.env, platform = process.platform, sid = undefined) {
  const sessionId = sid || resolveSessionId(agent)
  const { command, args } = launchTarget(binding, entry, 'remote', env, platform)
  return {
    serverName: 'remote-dev', transport: 'stdio', command, args,
    cwd: agent?.session?.header?.cwd || agent?.cwd || path.dirname(entry),
    env: buildChildEnv(env, { MINDIE_DSH_CONFIG: binding.configFile, MINDIE_DSH_STATE_DIR: binding.stateDir, REMOTE_DEV_STATE_DIR: path.join(binding.stateDir, 'remote'), REMOTE_DEV_HARNESS_PROFILE: 'dsh', REMOTE_DEV_SESSION_ID: sessionId, CLAUDE_SESSION_ID: '', CLAUDE_CODE_SESSION_ID: '', CODEX_SESSION_ID: '', CODEX_RUN_ID: '', CODEX_THREAD_ID: '' }, platform),
    toolCallTimeoutMs: remoteToolCallTimeoutMs(env),
    failOnStartupError: true,
  }
}
function entryFor(binding) {
  let installed = {}
  try { installed = JSON.parse(readFileSync(binding.configFile, 'utf8').replace(/^\uFEFF/, '')) }
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
      const mounted = new Map(), agentRecords = new WeakMap()
      const attach = async (agent) => {
        if (!agent || typeof agent.ctx?.plugin !== 'function') return
        const prevAgentRec = agentRecords.get(agent)
        if (prevAgentRec && !prevAgentRec.closed) return
        const sid = resolveSessionId(agent)
        const prevSidRec = mounted.get(sid)
        if (prevSidRec && !prevSidRec.closed) return
        const record = {
          sid, closed: false, remoteFiber: undefined, tools: undefined,
          async dispose() {
            record.closed = true
            if (mounted.get(sid) === record) mounted.delete(sid)
            if (agentRecords.get(agent) === record) agentRecords.delete(agent)
            const t = record.tools, r = record.remoteFiber
            record.tools = undefined; record.remoteFiber = undefined
            try { await t?.dispose?.() } finally { await r?.dispose?.() }
          },
        }
        agentRecords.set(agent, record)
        mounted.set(sid, record)
        try {
          record.remoteFiber = await agent.ctx.plugin(remote, remoteMcpConfig(binding, entry, agent, process.env, process.platform, sid))
          if (record.closed) { await record.dispose(); return }
          record.tools = await agent.ctx.plugin(remote, componentMcpConfig(binding, entry, agent, process.env, process.platform, sid))
          if (record.closed) { await record.dispose(); return }
        } catch (error) {
          await record.dispose()
          throw error
        }
      }
      const detach = async (agent) => {
        const record = (agent && agentRecords.get(agent)) || (agent ? mounted.get(resolveSessionId(agent)) : undefined)
        if (record) await record.dispose()
      }
      child.on('agent/created', ({ agent }) => { attach(agent).catch(() => {}) })
      child.on('agent/disposed', ({ agent }) => { detach(agent).catch(() => {}) })
      for (const agent of child.agents.list()) await attach(agent)
      child.effect(() => async () => { for (const record of [...mounted.values()]) await record.dispose() })
    },
  }
  return ctx.plugin(generationPlugin)
}
export async function apply(ctx, config) {
  const binding = runtimePaths()
  await mount(ctx, entryFor(binding), config, binding)
}
export default { name, inject, apply }
