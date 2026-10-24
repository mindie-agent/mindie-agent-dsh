// Lifecycle unit contract: one canonical binding moves host and remote together.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

test('an idle generation switch replaces both native host and remote MCP', async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-generation-'))
  const prior = { ...process.env }
  const events = globalThis.__mindieGenerationEvents = []
  let running = false
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
      globalThis.__mindieRemoteProfile = config.env?.REMOTE_DEV_HARNESS_PROFILE;
      globalThis.__mindieGenerationEvents.push(['remote', config.command]);
      ctx.effect(() => () => globalThis.__mindieGenerationEvents.push(['remote-dispose', config.command]));
    }
  `)
  const publish = (index) => writeFileSync(path.join(config, 'config.json'), JSON.stringify({
    package_dir: generations[index], python: path.join(root, 'python-' + index),
    venv_dir: path.join(root, 'runtime-' + index),
  }))
  function context() {
    const effects = []
    return {
      agents: { list: () => [{ status: running ? 'running' : 'idle' }] },
      effect(factory) { effects.push(factory()) },
      async plugin(plugin, options) {
        const child = context()
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
  try {
    publish(0)
    const launcher = await import('../host.mjs')
    await launcher.apply(ctx, { mcpPluginUrl: pathToFileURL(remote).href })
    assert.deepEqual(events, [['host', 'one'], ['remote', path.join(root, 'python-0')]])
    assert.equal(globalThis.__mindieRemoteProfile, 'dsh')
    publish(1)
    running = true
    await poll()
    assert.equal(events.length, 2)
    running = false
    await poll()
    assert.deepEqual(events.slice(2), [
      ['remote-dispose', path.join(root, 'python-0')], ['host-dispose', 'one'],
      ['host', 'two'], ['remote', path.join(root, 'python-1')],
    ])
    await poll()
    assert.equal(events.length, 6)
  } finally {
    for (const dispose of ctx.effects.toReversed()) await dispose?.()
    process.env = prior
    delete globalThis.__mindieGenerationEvents
    delete globalThis.__mindieRemoteProfile
    rmSync(root, { recursive: true, force: true })
  }
})
