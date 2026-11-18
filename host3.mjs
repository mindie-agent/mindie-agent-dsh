// MindIE Agent DSH Host plugin.
//
// [Role] Registers session-aware knowledge tools, the Stop handoff, and the
// loopback summary endpoint that serves both the legacy summary request and
// the explicit mindie-distill-request/1 C6 worker request.
// [Invariants] Unknown payload schemas are rejected. A C6 decision schema is
// prompt-carried because the DSH SDK has no constrained-JSON option; the
// organizer validates the returned decision with the shared runtime.
// [Timeouts & Locks] Existing update/setup timers and capture budgets are
// unchanged; the summary handler keeps its 512 KiB request-body cap.
// [Side-Effect Policy] The host calls only the active DSH model route and the
// sidecar; the host log is bounded by atomic rotation, and knowledge mutation,
// redaction and publication stay in the shared mindie-knowledge components.

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, unlinkSync, writeFileSync, renameSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import { homedir } from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { appendBoundedLine } from './scripts/log_rotate.mjs'
import {
  createSummaryServer,
  dispatchSummaryPayload,
  distillCallTimeoutMs,
  entryDescription,
  entryParameters,
  entryView,
  registerSessionKnowledgeSuite,
} from './scripts/knowledge_tools.mjs'

export {
  createSummaryHandler,
  createSummaryServer,
  dispatchSummaryPayload,
  summarizeWithLlm,
} from './scripts/knowledge_tools.mjs'

const require = createRequire(import.meta.url)
const { paths: runtimePaths, configHome, backgroundSpawnOptions, buildChildEnv } = require('./paths.cjs')

const VERSION = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
).version
const CALL_TIMEOUT_MS = 2 * 60 * 1000
// Two admission reads, 120s CLI work, 5s process cleanup and framing headroom.
const SYNC_TIMEOUT_MS = 140 * 1000
// Six-second shared revocation budget plus local status/cleanup headroom.
const DEACTIVATE_TIMEOUT_MS = 30 * 1000

let tmpSeq = 0

function atomicWriteJson(target, data, indent = 0) {
  mkdirSync(path.dirname(target), { recursive: true })
  const temporary = `${target}.${process.pid}.${++tmpSeq}.tmp`
  const body = indent > 0 ? JSON.stringify(data, null, indent) + '\n' : JSON.stringify(data)
  try {
    writeFileSync(temporary, body, { mode: 0o600 })
    renameSync(temporary, target)
  } catch (error) {
    try { unlinkSync(temporary) } catch {}
    throw error
  }
}

function setupCommand() {
  const p = runtimePaths()
  return `uv run --quiet --no-project --python 3.11 "${p.setupScript}"`
}

function log(line) {
  try {
    console.error(`[mindie-agent] ${line}`)
  } catch {
    // console may be detached during shutdown
  }
}

class Sidecar {
  constructor() {
    this.child = undefined
    this.pending = new Map()
    this.nextId = 1
    this.buffer = ''
  }

  start() {
    if (this.child) return this.child
    const p = runtimePaths()
    if (!existsSync(p.venvPython)) {
      throw new Error(`MindIE runtime is not initialized; run: ${setupCommand()}`)
    }
    const child = spawn(p.venvPython, ['-X', 'utf8', path.join(p.packageDir, 'scripts', 'mindie_sidecar.py')], {
      cwd: p.packageDir,
      env: buildChildEnv(process.env, {
        MINDIE_DSH_CONFIG: p.configFile,
        MINDIE_DSH_ENGINE_CONFIG: p.engineConfig,
        MINDIE_DSH_STATE_DIR: p.stateDir,
        MINDIE_DIAGNOSTICS_CONFIG: path.join(p.configHome, 'diagnostics.json'),
      }),
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    this.child = child
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk) => this.onData(chunk))
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk) => {
      const text = String(chunk).trim()
      if (text) log(text.slice(0, 2000))
    })
    const fail = (reason) => {
      if (this.child === child) this.child = undefined
      for (const { reject, timer } of this.pending.values()) {
        clearTimeout(timer)
        reject(new Error(`MindIE sidecar stopped: ${reason}`))
      }
      this.pending.clear()
    }
    child.on('error', (error) => fail(error.message))
    child.on('exit', (code, signal) => fail(`exit ${code ?? signal ?? 'unknown'}`))
    return child
  }

  onData(chunk) {
    this.buffer += chunk
    while (true) {
      const index = this.buffer.indexOf('\n')
      if (index < 0) break
      const line = this.buffer.slice(0, index)
      this.buffer = this.buffer.slice(index + 1)
      if (!line.trim()) continue
      let message
      try {
        message = JSON.parse(line)
      } catch {
        log('sidecar emitted non-JSON output')
        continue
      }
      const entry = this.pending.get(message.id)
      if (!entry) continue
      this.pending.delete(message.id)
      clearTimeout(entry.timer)
      if (message.ok) entry.resolve(message.result)
      else {
        const error = new Error(message.error?.message || 'MindIE operation failed')
        if (message.error?.code) error.code = message.error.code
        error.type = message.error?.type
        if (message.error?.diagnostic) error.diagnostic = message.error.diagnostic
        entry.reject(error)
      }
    }
  }

  call(op, args, signal, timeoutMs = op === 'configure' ? 600000 : CALL_TIMEOUT_MS) {
    const child = this.start()
    return new Promise((resolve, reject) => {
      const id = this.nextId++
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`MindIE ${op} timed out`))
      }, timeoutMs)
      const abort = () => {
        this.pending.delete(id)
        clearTimeout(timer)
        reject(new Error(`MindIE ${op} cancelled`))
      }
      if (signal) {
        if (signal.aborted) return abort()
        signal.addEventListener?.('abort', abort, { once: true })
      }
      this.pending.set(id, {
        resolve: (value) => {
          signal?.removeEventListener?.('abort', abort)
          resolve(value)
        },
        reject: (error) => {
          signal?.removeEventListener?.('abort', abort)
          reject(error)
        },
        timer,
      })
      try {
        child.stdin.write(JSON.stringify({ id, op, args }) + '\n')
      } catch (error) {
        clearTimeout(timer)
        this.pending.delete(id)
        reject(error)
      }
    })
  }

  dispose() {
    if (!this.child) return
    const child = this.child
    this.child = undefined
    try {
      child.stdin.end()
      child.kill()
    } catch {
      // already gone
    }
  }
}

const TOOL_OUTPUT = {
  schema: { type: 'string' },
  render(_args, value) {
    return [{ type: 'text', text: String(value) }]
  },
}

function text(value) {
  return JSON.stringify(value, null, 2)
}

function sessionOf(exec) {
  const session = exec?.agent?.session
  if (!session || typeof session.id !== 'string') {
    throw new Error('MindIE tools require an agent session')
  }
  return session
}

function cwdOf(session) {
  const cwd = session.header?.cwd
  if (typeof cwd !== 'string' || !path.isAbsolute(cwd)) {
    throw new Error('MindIE requires the session cwd')
  }
  return cwd
}

function optionalCwdOf(session) {
  const cwd = session.header?.cwd
  return typeof cwd === 'string' && path.isAbsolute(cwd) ? cwd : undefined
}

function tool(name, description, parameters, execute) {
  return { name, description, parameters, output: TOOL_OUTPUT, execute }
}

export const name = 'mindie-agent-dsh'
export const inject = ['tools', 'agents', 'llm']

export function apply(ctx) {
  const sidecar = new Sidecar()
  const sessions = new Map()
  const sessionRoutes = new Map()
  const p = runtimePaths()
  const endpointFile = path.join(p.stateDir, 'summary-endpoint.json')
  const endpointToken = randomBytes(32).toString('hex')
  let latestRoute
  let endpointUrl
  let summaryServer

  const writeEndpoint = () => {
    if (!endpointUrl) return
    const value = {
      schema: 'mindie-dsh-summary-endpoint/1',
      url: endpointUrl,
      token: endpointToken,
      provider: latestRoute?.provider || '',
      model: latestRoute?.model || '',
      routes: Object.fromEntries(sessionRoutes),
      pid: process.pid,
    }
    try {
      atomicWriteJson(endpointFile, value)
    } catch (error) {
      log(`summary endpoint file write failed: ${error.message}`)
    }
  }

  const setRoute = async (agent) => {
    const provider = agent?.options?.provider
    const model = agent?.options?.model
    if (typeof provider === 'string' && provider && typeof model === 'string' && model) {
      const nextRoute = { provider, model }
      const sessionId = agent?.session?.id
      if (typeof sessionId === 'string' && sessionId) {
        sessionRoutes.set(sessionId, nextRoute)
      }
      latestRoute = nextRoute
      await ensureSummaryServer()
      writeEndpoint()
    }
  }

  const resolveSummaryRoute = (payload) => {
    const sessionId = payload?.session_id
    if (typeof sessionId === 'string' && sessionRoutes.has(sessionId)) {
      return sessionRoutes.get(sessionId)
    }
    const policy = payload?.request?.policy_identity
    if (policy && typeof policy.model === 'string' && typeof policy.implementation?.provider === 'string') {
      for (const candidate of sessionRoutes.values()) {
        if (candidate.model === policy.model && candidate.provider === policy.implementation.provider) {
          return candidate
        }
      }
    }
    return latestRoute
  }

  const summarize = (payload) => dispatchSummaryPayload(ctx.llm, resolveSummaryRoute(payload), payload)


  const updateFile = path.join(p.configHome, 'update.json')
  const updateScript = path.join(p.dataHome, 'update', 'updater.py')
  const updateStatusFile = path.join(p.dataHome, 'update', 'status.json')
  const updateHostLog = path.join(p.dataHome, 'update', 'host.log')
  const hostLog = (message) => {
    try {
      appendBoundedLine(updateHostLog, new Date().toISOString() + ' ' + message)
    } catch (error) {
      console.error(`[mindie-agent] host log write failed: ${error.message}`)
    }
  }
  const profileName = () => {
    const index = process.argv.indexOf('--profile')
    if (index >= 0 && typeof process.argv[index + 1] === 'string' && process.argv[index + 1]) {
      return process.argv[index + 1]
    }
    const match = String(import.meta.url).match(/[/\\]profiles[/\\]([^/\\]+)[/\\]node_modules[/\\]/)
    return match ? match[1] : String(process.env.MINDIE_DSH_PROFILE || process.env.DSH_PROFILE || 'web')
  }

  const readUpdateConfig = () => {
    let value
    try {
      value = JSON.parse(readFileSync(updateFile, 'utf8'))
    } catch (error) {
      if (error?.code === 'ENOENT') return { enabled: true }
      log(`update configuration unreadable: ${error.message}`)
      return { enabled: false, config_error: String(error.message) }
    }
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      const kind = Array.isArray(value) ? 'array' : value === null ? 'null' : typeof value
      throw new Error(`update configuration must be a JSON object, got ${kind}`)
    }
    return value
  }

  const updateStatus = async () => {
    try {
      return JSON.parse(readFileSync(updateStatusFile, 'utf8'))
    } catch (error) {
      if (error?.code === 'ENOENT') {
        return { schema: 'mindie-dsh-update-status/1', state: 'never' }
      }
      log(`update status unreadable: ${error.message}`)
      return {
        schema: 'mindie-dsh-update-status/1',
        state: 'unreadable',
        error: String(error.message),
      }
    }
  }

  const writeUpdateEnabled = async (enabled) => {
    const current = readUpdateConfig()
    const next = { ...current, enabled: Boolean(enabled), interval_minutes: current.interval_minutes || 5 }
    atomicWriteJson(updateFile, next, 2)
    return next
  }

  const startUpdateCheck = async () => {
    if (!existsSync(updateScript)) return false
    const updateConfig = readUpdateConfig()
    const enabled = Object.hasOwn(updateConfig, 'enabled') ? updateConfig.enabled : true
    if (typeof enabled !== 'boolean') {
      throw new Error(`update configuration enabled must be a boolean, got ${typeof enabled}`)
    }
    if (!enabled) return false
    const agents = typeof ctx.agents?.list === 'function' ? ctx.agents.list() : []
    if (agents.some((agent) => agent?.status === 'running')) return false
    const profile = profileName()
    if (!profile) return false
    hostLog(`update check started profile=${profile}`)
    try {
      // Never pass detached=true on win32 (which spawns a visible console window
      // in Node); backgroundSpawnOptions sets windowsHide=true and detached=(platform !== 'win32').
      const child = spawn(
        p.venvPython,
        ['-X', 'utf8', updateScript, 'check', '--profile', profile, '--current-version', VERSION, '--silent'],
        { ...backgroundSpawnOptions(), windowsHide: true, stdio: 'ignore', env: buildChildEnv(process.env, {}) },
      )
      child.unref()
      return true
    } catch (error) {
      log(`update check could not start: ${error.message}`)
      return false
    }
  }

  let updateTimer
  let updateInterval
  try {
    const config = readUpdateConfig()
    updateTimer = setTimeout(() => {
      startUpdateCheck().catch((error) => log(`update check failed: ${error.message}`))
    }, 30000)
    updateTimer.unref?.()
    const minutes = Number(config.interval_minutes) >= 1 ? Number(config.interval_minutes)
      : Number(config.interval_hours) >= 1 ? Number(config.interval_hours) * 60 : 5
    updateInterval = setInterval(() => {
      startUpdateCheck().catch((error) => log(`update check failed: ${error.message}`))
    }, minutes * 60 * 1000)
    updateInterval.unref?.()
  } catch (error) {
    log(`update timer could not start: ${error.message}`)
  }

  async function ensureSummaryServer() {
    if (summaryServer) return
    summaryServer = createSummaryServer({ summarize, token: endpointToken })
    await new Promise((resolve, reject) => {
      summaryServer.once('error', reject)
      summaryServer.listen(0, '127.0.0.1', resolve)
    })
    summaryServer.on('error', (error) => log(`summary server failed: ${error.message}`))
    endpointUrl = `http://127.0.0.1:${summaryServer.address().port}/summary`
    writeEndpoint()
  }
  const deactivatedSessions = new Set()
  let disposeGlobalKnowledgeTools

  ctx.effect(() => () => {
    clearTimeout(updateTimer)
    clearInterval(updateInterval)
    try { disposeGlobalKnowledgeTools?.() } catch {}
    deactivatedSessions.clear()
    sessionRoutes.clear()
    latestRoute = undefined
    sidecar.dispose()
    sessions.clear()
    try {
      summaryServer?.close()
    } catch {
      // already closed
    }
    try {
      unlinkSync(endpointFile)
    } catch {
      // endpoint file was never written or is already gone
    }
  }, 'mindie-agent-dsh.sidecar')

  const remember = (session, result) => {
    if (!result.token) return undefined
    const state = {
      token: result.token,
      projectRoot: result.project_root,
      sharing: result.sharing,
      activatedAt: result.activated_at,
    }
    sessions.set(session.id, state)
    return state
  }

  const readJsonObject = (filePath) => {
    try {
      const parsed = JSON.parse(readFileSync(filePath, 'utf8'))
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : undefined
    } catch {
      return undefined
    }
  }

  const resume = async (session) => {
    if (sessions.get(session.id)?.token) return sessions.get(session.id)
    const curPaths = runtimePaths()
    const engDoc = readJsonObject(curPaths.engineConfig)
    const admPath = typeof engDoc?.admission_path === 'string' && engDoc.admission_path
      ? engDoc.admission_path
      : path.join(curPaths.dataHome, 'admission.sqlite3')
    try {
      statSync(admPath)
    } catch (error) {
      if (error.code === 'ENOENT') return undefined
      throw error
    }
    return remember(session, await sidecar.call('resume', { session: session.id }))
  }

  function requireState(exec, options = {}) {
    return (async () => {
      const session = sessionOf(exec)
      const cwd = options?.requireCwd ? cwdOf(session) : optionalCwdOf(session)
      if (deactivatedSessions.has(session.id)) {
        throw new Error('this task is not activated; session was explicitly deactivated')
      }
      let state = await resume(session)
      if (state?.token && cwd && state.projectRoot && state.projectRoot !== cwd) {
        sessions.delete(session.id)
        state = undefined
      }
      if (options?.withRoute) {
        await setRoute(exec.agent)
      }
      return { session, cwd, state }
    })()
  }

  const activate = async (session, cwd) => {
    const result = await sidecar.call('activate', { session: session.id, cwd })
    return remember(session, result)
  }

  const activateDomain = async (session, cwd, domain) => {
    // The sidecar re-binds the default identity first and then activates the
    // selected domain through the shared helper. Only the target summary is
    // returned; the default SESSION_STATE token above is never replaced.
    const result = await sidecar.call('activate', {
      session: session.id, cwd, domain,
    })
    const activation = result?.domain_activation
    if (!activation) {
      throw new Error('selected domain activation was not returned')
    }
    // The target credential stays in its own Admission. Never place it in
    // SESSION_STATE or return it from the public entry tool.
    if (activation.lease && typeof activation.lease === 'object') {
      delete activation.lease.token
    }
    delete activation.token
    return activation
  }

  ctx.tools.register(
    tool('mindie_entry', entryDescription, entryParameters, async (args, exec) => {
      const session = sessionOf(exec)
      const op = args.op
      if (args.domain !== undefined) {
        if (op !== 'init') {
          throw new Error('domain is supported only for mindie_entry op=init')
        }
        if (typeof args.domain !== 'string' || args.domain.length === 0) {
          throw new Error('domain must be a nonempty canonical name')
        }
      }
      if (op === 'init') {
        const cwd = cwdOf(session)
        deactivatedSessions.delete(session.id)
        const state = (await resume(session)) || (await activate(session, cwd))
        await setRoute(exec.agent)
        let activation
        if (args.domain !== undefined) {
          activation = await activateDomain(session, cwd, args.domain)
        }
        const view = entryView('init', state.sharing, state)
        if (activation) view.domain_activation = activation
        return text(view)
      }
      if (op === 'status' || op === 'sharing-status') {
        const cwd = optionalCwdOf(session)
        const result = await sidecar.call('status', { session: session.id, ...(cwd ? { cwd } : {}) })
        const state = sessions.get(session.id)
        if (state) state.sharing = result.sharing
        return text({ kind: 'status', activated: Boolean(result.lease?.enabled), ...result })
      }
      if (op === 'choose' || op === 'sharing-enable' || op === 'sharing-disable') {
        const cwd = cwdOf(session)
        if (deactivatedSessions.has(session.id)) {
          throw new Error('this task is not activated; session was explicitly deactivated')
        }
        const choice =
          op === 'sharing-disable' ? 'disabled' : op === 'sharing-enable' ? 'contribute' : args.choice
        if (choice !== 'contribute' && choice !== 'disabled') {
          throw new Error('choice must be contribute or disabled')
        }
        const result = await sidecar.call('configure', {
          choice,
          repository: args.repository,
          account: args.account,
          fork: args.fork,
          cwd,
        })
        const state = sessions.get(session.id)
        if (state) state.sharing = result.sharing
        return text(entryView('choose', result.sharing, state))
      }
      if (op === 'update-status') {
        return text(await updateStatus())
      }
      if (op === 'update-check') {
        const started = await startUpdateCheck()
        return text({ started, next: 'The background check writes status under the MindIE data directory.' })
      }
      if (op === 'update-enable' || op === 'update-disable') {
        const value = await writeUpdateEnabled(op === 'update-enable')
        return text({ update: value })
      }
      if (op === 'deactivate') {
        const cwd = optionalCwdOf(session)
        const result = await sidecar.call(
          'deactivate',
          { session: session.id, ...(cwd ? { cwd } : {}) },
          exec.signal,
          DEACTIVATE_TIMEOUT_MS,
        )
        const domains = Array.isArray(result.domains) ? result.domains : []
        const partial = (result.status && result.status !== 'inactive')
          || domains.some((item) => item?.status !== 'inactive')
        if (partial) {
          const error = new Error(text({
            status: result.status || 'partial',
            deactivated: false,
            changed: Boolean(result.changed),
            sharing: result.sharing,
            ...(result.sharing_error ? { sharing_error: result.sharing_error } : {}),
            domains,
          }))
          error.code = 'deactivate_partial'
          throw error
        }
        deactivatedSessions.add(session.id)
        sessions.delete(session.id)
        return text({
          deactivated: Boolean(result.deactivated),
          sharing: result.sharing,
          ...(result.status ? { status: result.status } : {}),
          ...(domains.length ? { domains } : {}),
          ...(result.sharing_error ? { sharing_error: result.sharing_error } : {}),
        })
      }
      if (op === 'reporting-status') {
        const result = await sidecar.call('reporting', {})
        return text(result)
      }
      throw new Error(`unsupported mindie_entry op: ${String(op)}`)
    }),
  )

  disposeGlobalKnowledgeTools = registerSessionKnowledgeSuite(ctx.tools, {
    tool,
    sidecar,
    requireState,
    text,
    syncTimeoutMs: SYNC_TIMEOUT_MS,
    timeoutMs: distillCallTimeoutMs,
  })

  const findTranscript = (sessionId) => {
    const home = process.env.DSH_HOME || path.join(homedir(), '.dsh')
    const root = path.join(home, 'sessions')
    try {
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue
        const candidate = path.join(root, entry.name, sessionId, 'session.v4.jsonl.zstd')
        if (existsSync(candidate)) return candidate
      }
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        log(`transcript directory unreadable: ${error.message}`)
      }
    }
    return undefined
  }

  const canonicalPath = (target) => {
    try {
      return realpathSync(target)
    } catch {
      return path.resolve(target)
    }
  }

  const isStopCaptureAuthorized = (cwd) => {
    if (typeof cwd !== 'string' || !path.isAbsolute(cwd)) return false
    const curPaths = runtimePaths()
    const engDoc = readJsonObject(curPaths.engineConfig)
    const commPath = typeof engDoc?.community_config === 'string' && engDoc.community_config
      ? engDoc.community_config
      : path.join(curPaths.configHome, 'mindie-community.json')
    const comm = readJsonObject(commPath)
    if (!comm || comm.enabled !== true || !Array.isArray(comm.project_roots) || !comm.project_roots.length) {
      return false
    }
    const consentPath = typeof comm.consent_config === 'string' && comm.consent_config
      ? comm.consent_config
      : path.join(curPaths.configHome, 'mindie-consent.json')
    const consent = readJsonObject(consentPath)
    if (!consent || consent.choice !== 'contribute') return false
    const norm = (val) => process.platform === 'win32' ? val.toLowerCase() : val
    const target = norm(canonicalPath(cwd))
    return comm.project_roots.some((root) => {
      if (typeof root !== 'string' || !path.isAbsolute(root)) return false
      const base = norm(canonicalPath(root))
      const rel = path.relative(base, target)
      return rel === '' || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel))
    })
  }

  const spawnCapture = (session, turn, state) => {
    const p = runtimePaths()
    const transcript = findTranscript(session.id)
    if (!transcript) {
      log(`capture skipped: no transcript for session ${session.id}`)
      return
    }
    const event = {
      hook_event_name: 'Stop',
      stop_hook_active: false,
      session_id: session.id,
      identity_kind: 'turn',
      turn_id: String(turn),
      transcript_path: transcript,
      ...(state?.token ? { mindie_activation: state.token } : {}),
      harness: 'dsh',
      cwd: cwdOf(session),
    }
    try {
      const child = spawn(p.venvPython, ['-X', 'utf8', path.join(p.packageDir, 'scripts', 'capture_stop.py')], {
        cwd: p.packageDir,
        env: buildChildEnv(process.env, {
          MINDIE_DSH_CONFIG: p.configFile,
          MINDIE_DSH_STATE_DIR: p.stateDir,
          MINDIE_DIAGNOSTICS_CONFIG: path.join(p.configHome, 'diagnostics.json'),
        }),
        ...backgroundSpawnOptions(),
        windowsHide: true,
        stdio: ['pipe', 'ignore', 'ignore'],
      })
      child.on('error', (error) => log(`capture spawn failed: ${error.message}`))
      child.stdin.on('error', () => {})
      child.stdin.end(JSON.stringify(event) + '\n')
      child.unref()
    } catch (error) {
      log(`capture failed: ${error.message}`)
    }
  }

  ctx.on('agent/pre-step', (_event, next) => next())

  ctx.on('agent/disposed', ({ agent }) => {
    const sessionId = agent?.session?.id
    if (typeof sessionId === 'string' && sessionId) {
      sessions.delete(sessionId)
      deactivatedSessions.delete(sessionId)
      if (sessionRoutes.delete(sessionId)) {
        latestRoute = undefined
        for (const remaining of sessionRoutes.values()) latestRoute = remaining
        writeEndpoint()
      }
    }
  })

  ctx.on('agent/turn-stopping', ({ agent, turn }) => {
    try {
      const session = agent?.session
      if (!session || typeof session.id !== 'string') return
      if (Number(session.header?.delegationDepth || 0) > 0) return
      if (deactivatedSessions.has(session.id)) return
      const cwd = optionalCwdOf(session)
      if (!cwd || !isStopCaptureAuthorized(cwd)) return
      setRoute(agent).catch((error) => log(`summary route setup failed: ${error.message}`))
      spawnCapture(session, turn, sessions.get(session.id))
    } catch (error) {
      log(`turn-stopping handler failed: ${error.message}`)
    }
  })

  // Expose a small read-only status for diagnostics; never called by the model.
  return { version: VERSION, configHome: configHome() }
}

// Default object shape as well as named exports: the Loader normalizes
// ESM/CJS default exports before reading `inject`.
export default { name, inject, apply }
