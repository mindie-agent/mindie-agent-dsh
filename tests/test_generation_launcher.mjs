// Lifecycle unit contract: remote MCP is scoped to each real agent session.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

test('an idle generation switch replaces host and per-session remote MCP', async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-generation-'))
  const prior = { ...process.env }
  const events = globalThis.__mindieGenerationEvents = []
  let poll
  t.mock.method(globalThis, 'setInterval', (callback) => {
    poll = callback
    return { unref() {} }
  })
  t.mock.method(globalThis, 'clearInterval', () => {})
  const config = path.join(root, 'config')
  const data = path.join(root, 'data')
  mkdirSync(config, { recursive: true })
  mkdirSync(path.join(data, 'update'), { recursive: true })
  process.env.MINDIE_DSH_CONFIG_HOME = config
  process.env.MINDIE_DSH_DATA_HOME = data
  delete process.env.MINDIE_DSH_CONFIG
  const generations = ['one', 'two'].map((name) => {
    const directory = path.join(root, name)
    mkdirSync(directory)
    writeFileSync(path.join(directory, 'host3.mjs'), `
      export const name = 'fixture-host';
      export function apply(ctx) {
        globalThis.__mindieGenerationEvents.push(['host', ${JSON.stringify(name)}]);
        ctx.effect(() => () => globalThis.__mindieGenerationEvents.push(['host-dispose', ${JSON.stringify(name)}]));
      }
    `)
    return directory
  })
  const remote = path.join(root, 'remote.mjs')
  writeFileSync(remote, `
    export const name = 'fixture-mcp';
    export function apply(ctx, config) {
      globalThis.__mindieGenerationEvents.push([
        config.serverName, config.command,
        config.env?.REMOTE_DEV_SESSION_ID || config.env?.MINDIE_DSH_SESSION_ID,
        config.serverName === 'remote-dev' ? config.env?.REMOTE_DEV_HARNESS_PROFILE : config.args[0],
      ]);
      ctx.effect(() => () => globalThis.__mindieGenerationEvents.push(
        [config.serverName + '-dispose', config.command,
         config.env?.REMOTE_DEV_SESSION_ID || config.env?.MINDIE_DSH_SESSION_ID]));
    }
  `)
  const publish = (index) => writeFileSync(path.join(config, 'config.json'), JSON.stringify({
    package_dir: generations[index], python: path.join(root, 'python-' + index),
    venv_dir: path.join(root, 'runtime-' + index),
  }))
  const agents = []
  function context(children = []) {
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
        const child = context(children)
        children.push(child)
        await plugin.apply(child, options)
        const fiber = { dispose: async () => {
          for (const dispose of child.effects.toReversed()) await dispose?.()
        } }
        effects.push(() => fiber.dispose())
        return fiber
      },
      effects,
    }
  }
  const ctx = context()
  const makeAgent = (id) => {
    const agent = { session: { id }, ctx: context(), status: 'idle' }
    agents.push(agent)
    return agent
  }
  try {
    publish(0)
    const launcher = await import('../host.mjs')
    await launcher.apply(ctx, { mcpPluginUrl: pathToFileURL(remote).href })
    assert.deepEqual(events, [['host', 'one']])
    const createdHandler = () => [...ctx.children].reverse().find(
      (child) => child.listeners.has('agent/created'))
    assert.ok(createdHandler(), 'generation child context')
    const created = createdHandler().listeners.get('agent/created')
    assert.equal(typeof created, 'function')

    const first = makeAgent('session-one')
    await created({ agent: first })
    assert.deepEqual(events.slice(1), [
      ['remote-dev', path.join(root, 'python-0'), 'session-one', 'dsh'],
      ['mindie-tools', path.join(root, 'python-0'), 'session-one',
        path.join(generations[0], 'scripts', 'component_mcp.py')],
    ])

    publish(1)
    first.status = 'running'
    await poll()
    assert.equal(events.length, 3)
    first.status = 'idle'
    await poll()
    assert.deepEqual(events.slice(3), [
      ['mindie-tools-dispose', path.join(root, 'python-0'), 'session-one'],
      ['remote-dev-dispose', path.join(root, 'python-0'), 'session-one'],
      ['host-dispose', 'one'],
      ['host', 'two'],
      ['remote-dev', path.join(root, 'python-1'), 'session-one', 'dsh'],
      ['mindie-tools', path.join(root, 'python-1'), 'session-one',
        path.join(generations[1], 'scripts', 'component_mcp.py')],
    ])

    const second = makeAgent('session-two')
    await createdHandler().listeners.get('agent/created')({ agent: second })
    assert.deepEqual(events.slice(9), [
      ['remote-dev', path.join(root, 'python-1'), 'session-two', 'dsh'],
      ['mindie-tools', path.join(root, 'python-1'), 'session-two',
        path.join(generations[1], 'scripts', 'component_mcp.py')],
    ])
    await poll()
    assert.equal(events.length, 11)
  } finally {
    for (const dispose of ctx.effects.toReversed()) await dispose?.()
    process.env = prior
    delete globalThis.__mindieGenerationEvents
    rmSync(root, { recursive: true, force: true })
  }
})
