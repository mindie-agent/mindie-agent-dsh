// Exercise production launcher configuration with the shipped DSH MCP client.
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

if (process.env.NODE_TEST_CONTEXT) process.exit(0)
const require = createRequire(path.join(process.env.DSH_MODULE_ROOT, '@deepseek-ai/dsh-mcp-client/package.json'))
const generation = path.join(process.cwd(), 'native-generation')
mkdirSync(generation)
writeFileSync(path.join(generation, 'host3.mjs'), 'export function apply() {}\n')
symlinkSync(path.join(process.env.COMPONENT_PACKAGE, 'scripts'), path.join(generation, 'scripts'), 'junction')
const adapterPath = process.env.MINDIE_DSH_CONFIG
const adapter = JSON.parse(readFileSync(adapterPath, 'utf8'))
writeFileSync(adapterPath, JSON.stringify({ ...adapter, package_dir: generation }))
const agents = []
const registrations = []
function context(tools = new Map(), children = []) {
  const disposers = []
  const listeners = new Map()
  return {
    root: {},
    agents: { list: () => agents },
    tools: { register(tool) {
      if (tools.has(tool.name)) throw new Error('duplicate tool: ' + tool.name)
      tools.set(tool.name, tool)
      return () => tools.delete(tool.name)
    } },
    effect(callback) { disposers.push(callback()) },
    inject() {},
    on(event, callback) { listeners.set(event, callback) },
    get() { return undefined },
    logger: { info() {}, warn: console.error, error: console.error },
    listeners, children, disposers,
    async plugin(plugin, config) {
      const child = context(tools, children)
      children.push(child)
      if (config?.serverName) registrations.push(config.serverName)
      await plugin.apply(child, config)
      let disposed = false
      const fiber = { async dispose() {
        if (disposed) return
        disposed = true
        for (const dispose of child.disposers.toReversed()) await dispose?.()
      } }
      disposers.push(() => fiber.dispose())
      return fiber
    },
  }
}
const tools = new Map()
const ctx = context()
const agent = { session: { id: 'native-contract', header: { cwd: process.cwd() } },
  status: 'idle', ctx: context(tools) }
try {
  const launcher = await import('../host.mjs')
  await launcher.apply(ctx, {
    mcpPluginUrl: pathToFileURL(require.resolve('@deepseek-ai/dsh-mcp-client')).href,
  })
  const lifecycle = ctx.children.find((child) => child.listeners.has('agent/created'))
  await lifecycle.listeners.get('agent/created')({ agent })
  assert.deepEqual(registrations, ['remote-dev', 'mindie-tools'])
  assert.ok([...tools.keys()].some((name) => name.startsWith('mcp__remote-dev__remote_')))
  const status = await tools.get('mcp__mindie-tools__mindie_status').execute(
    {}, { signal: new AbortController().signal })
  assert.equal(status.structuredContent.status, 'configured')
  const inventory = await tools.get('mcp__mindie-tools__profiler').execute(
    { argv: ['inventory', '--root', process.env.COMPONENT_CAPTURE] },
    { signal: new AbortController().signal })
  assert.equal(inventory.structuredContent.result.rank_count, 1)
  assert.ok(inventory.structuredContent.result.ranks[0].files.length)
  console.log(JSON.stringify({
    names: [...tools.keys()], status: status.structuredContent.status, rank_count: 1,
  }))
  await lifecycle.listeners.get('agent/disposed')({ agent })
  assert.equal(tools.size, 0)
} finally {
  for (const dispose of ctx.disposers.toReversed()) await dispose?.()
}
