// Unit contract: one remote MCP/state root per real agent session, and an
// outer timeout above the native 600000 ms maximum. Real-host acceptance is
// exercised separately through the installed DSH runtime.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

function context(children = [], agents = []) {
  const effects = []
  const listeners = new Map()
  return {
    agents: { list: () => agents },
    effect(factory) { effects.push(factory()) },
    on(name, handler) {
      listeners.set(name, handler)
      return () => listeners.delete(name)
    },
    listeners,
    children,
    async plugin(plugin, options) {
      const child = context(children, agents)
      children.push(child)
      await plugin.apply(child, options)
      const fiber = {
        dispose: async () => {
          for (const dispose of child.effects.toReversed()) await dispose?.()
        },
      }
      effects.push(() => fiber.dispose())
      return fiber
    },
    effects,
  }
}

test('remote MCP is scoped per agent session and disposed with the agent', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-isolation-'))
  const configHome = path.join(root, 'config')
  const dataHome = path.join(root, 'data')
  mkdirSync(configHome, { recursive: true })
  mkdirSync(path.join(dataHome, 'update'), { recursive: true })
  process.env.MINDIE_DSH_CONFIG_HOME = configHome
  process.env.MINDIE_DSH_DATA_HOME = dataHome
  delete process.env.MINDIE_DSH_CONFIG

  const generation = path.join(root, 'generation')
  mkdirSync(generation)
  writeFileSync(path.join(generation, 'host3.mjs'), `
    export const name = 'fixture-host';
    export function apply(ctx) {}
  `)
  const remote = path.join(root, 'remote.mjs')
  writeFileSync(remote, `
    export const name = 'fixture-mcp';
    export function apply(ctx, config) {
      globalThis.__mindieRemoteConfigs.push({
        server: config.serverName,
        session: config.env.REMOTE_DEV_SESSION_ID || config.env.MINDIE_DSH_SESSION_ID,
        state: config.env.REMOTE_DEV_STATE_DIR,
        timeoutMs: config.toolCallTimeoutMs,
        claude: config.env.CLAUDE_SESSION_ID,
        codex: config.env.CODEX_SESSION_ID,
        cwd: config.cwd,
      });
      ctx.effect(() => () => globalThis.__mindieRemoteDisposed.push(
        [config.serverName, config.env.REMOTE_DEV_SESSION_ID || config.env.MINDIE_DSH_SESSION_ID]));
    }
  `)
  writeFileSync(path.join(configHome, 'config.json'), JSON.stringify({
    package_dir: generation, python: process.execPath,
    venv_dir: path.join(root, 'runtime'),
  }))
  globalThis.__mindieRemoteConfigs = []
  globalThis.__mindieRemoteDisposed = []
  const agents = []
  const ctx = context([], agents)
  const makeAgent = (id) => {
    const agent = { session: { id, header: { cwd: root } }, ctx: context([], agents), status: 'idle' }
    agents.push(agent)
    return agent
  }
  const launcher = await import('../host.mjs')
  await launcher.apply(ctx, { mcpPluginUrl: pathToFileURL(remote).href })
  const generationChild = [...ctx.children].reverse().find(
    (child) => child.listeners.has('agent/created'))
  assert.ok(generationChild)
  const created = generationChild.listeners.get('agent/created')
  const disposed = generationChild.listeners.get('agent/disposed')

  const first = makeAgent('session-one')
  const second = makeAgent('session-two')
  await created({ agent: first })
  await created({ agent: second })
  const bySession = Object.fromEntries(
    globalThis.__mindieRemoteConfigs.filter((item) => item.server === 'remote-dev')
      .map((item) => [item.session, item]))
  assert.deepEqual(Object.keys(bySession).sort(), ['session-one', 'session-two'])
  assert.notEqual(bySession['session-one'].state, bySession['session-two'].state)
  assert.match(bySession['session-one'].state, /remote[/\\]session-one$/)
  assert.match(bySession['session-two'].state, /remote[/\\]session-two$/)
  assert.equal(bySession['session-one'].timeoutMs, 660000)
  assert.ok(bySession['session-one'].timeoutMs > 600000)
  assert.equal(bySession['session-one'].claude, '')
  assert.equal(bySession['session-one'].codex, '')

  await disposed({ agent: second })
  assert.deepEqual(globalThis.__mindieRemoteDisposed, [
    ['mindie-tools', 'session-two'], ['remote-dev', 'session-two'],
  ])
  const components = globalThis.__mindieRemoteConfigs.filter((item) => item.server === 'mindie-tools')
  assert.equal(components.length, 2)
  assert.ok(components.every((item) => item.cwd === root && item.timeoutMs > 605000))
  assert.equal(launcher.remoteToolCallTimeoutMs({}), 660000)
  assert.equal(launcher.remoteToolCallTimeoutMs({
    MINDIE_DSH_REMOTE_TOOL_CALL_TIMEOUT_MS: '700000'}), 700000)
  assert.equal(launcher.remoteToolCallTimeoutMs({
    MINDIE_DSH_REMOTE_TOOL_CALL_TIMEOUT_MS: '600000'}), 660000)
  assert.equal(launcher.remoteToolCallTimeoutMs({
    MINDIE_DSH_NATIVE_TOOL_CALL_MAX_MS: '900000',
    MINDIE_DSH_REMOTE_CALL_GRACE_MS: '1000'}), 901000)
})
