import test from 'node:test'
import assert from 'node:assert/strict'
import { ChildProcess } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { apply } from '../host3.mjs'

function hostContext({ list = () => [] } = {}) {
  const tools = new Map()
  const disposers = []
  return {
    tools: { register(tool) { tools.set(tool.name, tool) } },
    agents: { list },
    llm: { async *stream() { throw new Error('unexpected model call') } },
    on() {},
    effect(callback) { disposers.push(callback()) },
    toolsMap: tools,
    dispose() {
      for (const dispose of disposers.reverse()) if (dispose) dispose()
    },
  }
}

async function withUpdateHost(options, fn) {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-dsh-update-host-'))
  const prior = { ...process.env }
  process.env.MINDIE_DSH_DATA_HOME = path.join(root, 'data')
  process.env.MINDIE_DSH_CONFIG_HOME = path.join(root, 'config')
  process.env.MINDIE_DSH_STATE_DIR = path.join(root, 'state')
  delete process.env.MINDIE_DSH_CONFIG
  mkdirSync(path.join(root, 'config'), { recursive: true })
  mkdirSync(path.join(root, 'data', 'update'), { recursive: true })
  writeFileSync(path.join(root, 'data', 'update', 'updater.py'), '# stub\n')
  if (Object.hasOwn(options, 'config')) {
    writeFileSync(
      path.join(root, 'config', 'update.json'),
      JSON.stringify(options.config, null, 2) + '\n',
    )
  }
  const ctx = hostContext(options)
  apply(ctx)
  const exec = { agent: { session: { id: 'update-session', header: { cwd: root } } } }
  try {
    await fn({ ctx, exec, root })
  } finally {
    ctx.dispose()
    process.env = prior
    rmSync(root, { recursive: true, force: true })
  }
}

test('boolean false keeps the background updater idle', async () => {
  let spawned = 0
  const original = ChildProcess.prototype.spawn
  ChildProcess.prototype.spawn = function spawnStub() {
    spawned += 1
    throw new Error('update process should not start')
  }
  try {
    await withUpdateHost({ config: { enabled: false, interval_minutes: 5 } }, async ({ ctx, exec }) => {
      const reply = JSON.parse(await ctx.toolsMap.get('mindie_entry').execute({ op: 'update-check' }, exec))
      assert.equal(reply.started, false)
      assert.equal(spawned, 0)
    })
  } finally {
    ChildProcess.prototype.spawn = original
  }
})

test('a non-object update.json is a configuration error, not a silent start', async () => {
  const original = ChildProcess.prototype.spawn
  ChildProcess.prototype.spawn = function spawnStub() {
    throw new Error('update process should not start')
  }
  try {
    for (const config of [[], null, true, 'false', 1]) {
      await withUpdateHost({ config }, async ({ ctx, exec }) => {
        await assert.rejects(
          ctx.toolsMap.get('mindie_entry').execute({ op: 'update-check' }, exec),
          /update configuration must be a JSON object/,
        )
      })
    }
  } finally {
    ChildProcess.prototype.spawn = original
  }
})

test('a string enabled flag is a configuration error, not a silent start', async () => {
  const original = ChildProcess.prototype.spawn
  ChildProcess.prototype.spawn = function spawnStub() {
    throw new Error('update process should not start')
  }
  try {
    await withUpdateHost({ config: { enabled: 'false', interval_minutes: 5 } }, async ({ ctx, exec }) => {
      await assert.rejects(
        ctx.toolsMap.get('mindie_entry').execute({ op: 'update-check' }, exec),
        /enabled must be a boolean, got string/,
      )
    })
  } finally {
    ChildProcess.prototype.spawn = original
  }
})

test('agents.list failure is not treated as a quiet idle skip', async () => {
  const original = ChildProcess.prototype.spawn
  ChildProcess.prototype.spawn = function spawnStub() {
    throw new Error('update process should not start')
  }
  try {
    await withUpdateHost({
      config: { enabled: true, interval_minutes: 5 },
      list() { throw new Error('agents.list unavailable') },
    }, async ({ ctx, exec }) => {
      await assert.rejects(
        ctx.toolsMap.get('mindie_entry').execute({ op: 'update-check' }, exec),
        /agents\.list unavailable/,
      )
    })
  } finally {
    ChildProcess.prototype.spawn = original
  }
})
