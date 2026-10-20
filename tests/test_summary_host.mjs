import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { createServer } from 'node:net'
import path from 'node:path'
import { apply } from '../scripts/summary_host.mjs'

const root = await mkdtemp(path.join(tmpdir(), 'mindie-summary-host-'))
const endpointFile = path.join(root, 'endpoint.json')
const reservation = createServer()
await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve))
const listenPort = reservation.address().port
await new Promise(resolve => reservation.close(resolve))
let calls = 0
let resolved
let dispose
let adaptersUpdated
let providers = []
const llm = {
  listProviders: () => providers,
  async resolveCallConfig(route) { resolved = route },
  async *stream(options) {
    calls++
    assert.equal(options.provider, 'fixture-provider')
    assert.equal(options.model, 'fixture-model')
    yield { type: 'text-delta', text: '{"blocks":[],"navigation":{"title":"T","summary":"S"}}' }
    yield { type: 'usage', usage: { inputTokens: 2, cacheReadTokens: 0, outputTokens: 3 } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
try {
  await apply({
    get(name) {
      if (name === 'llm') return llm
      assert.equal(name, 'agentDefaultModel')
      return { currentSelection: () => ({ provider: 'fixture-provider', model: 'fixture-model' }) }
    },
    on(event, callback) {
      assert.equal(event, 'llm/adapters-updated')
      adaptersUpdated = callback
    },
    effect(start) { dispose = start() },
  }, { endpointFile, listenPort })
  await assert.rejects(readFile(endpointFile), { code: 'ENOENT' })
  assert.equal(resolved, undefined)
  assert.equal(calls, 0)
  providers = [{ id: 'fixture-provider' }]
  await adaptersUpdated()
  await adaptersUpdated()
  const endpoint = JSON.parse(await readFile(endpointFile, 'utf8'))
  assert.equal(Number(new URL(endpoint.url).port), listenPort)
  assert.deepEqual(resolved, { provider: endpoint.provider, model: endpoint.model })
  assert.equal(calls, 0)
  assert.equal(endpoint.mode, 'summary-only')
  const forbidden = await fetch(endpoint.url, { method: 'POST', body: '{}' })
  assert.equal(forbidden.status, 403)
  assert.equal(calls, 0)
  const response = await fetch(endpoint.url, {
    method: 'POST', headers: { authorization: `Bearer ${endpoint.token}` },
    body: JSON.stringify({ request: {}, prompt: 'Fixture summary request.' }),
  })
  assert.equal(response.status, 200)
  assert.equal((await response.json()).model, 'fixture-model')
  assert.equal(calls, 1)
  await dispose()
  dispose = null
  await assert.rejects(readFile(endpointFile), { code: 'ENOENT' })
} finally {
  if (dispose) await dispose()
  await rm(root, { recursive: true, force: true })
}
console.log('summary-only SDK host passed: startup 0 model calls; one authenticated request; clean disposal')
