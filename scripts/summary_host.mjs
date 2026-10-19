// [Role] Serve the existing summary protocol in an explicitly owned SDK process.
// [Invariants] Native llm route validation precedes endpoint publication. Startup
// never creates an Agent/session or sends a model request; the existing organizer
// and SummaryLedger own authorization, invocation and uncertain outcomes.
// [Timeouts & Locks] SDK initialization/shutdown own the process budget. HTTP
// request handling reuses host3's existing authenticated and bounded protocol.
// [Side-Effect Policy] Writes only the explicitly supplied endpoint file and
// serves loopback. No tools, capture hooks, sidecar, updater or consent mutation.

import { createSummaryServer, dispatchSummaryPayload } from '../host3.mjs'
import { randomBytes } from 'node:crypto'
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import path from 'node:path'

export const name = 'mindie-dsh-summary-host'
export const inject = ['llm', 'agentDefaultModel']

export async function apply(ctx, config) {
  const endpointFile = config?.endpointFile
  if (typeof endpointFile !== 'string' || !path.isAbsolute(endpointFile)) {
    throw new Error('summary-only SDK host requires an explicit absolute endpointFile')
  }
  const listenPort = config?.listenPort ?? 0
  if (!Number.isInteger(listenPort) || listenPort < 0 || listenPort > 65535) {
    throw new Error('summary-only SDK host listenPort must be an integer from 0 through 65535')
  }
  const llm = ctx.get('llm')
  let startup
  let disposed = false
  let closeEndpoint
  const startWhenReady = () => {
    if (disposed || startup) return startup
    const selection = ctx.get('agentDefaultModel').currentSelection()
    if (!llm.listProviders().some(provider => provider.id === selection.provider)) return
    const route = { provider: selection.provider, model: selection.model }
    startup = openEndpoint(llm, route, endpointFile, listenPort).then(close => { closeEndpoint = close })
    return startup
  }
  ctx.on('llm/adapters-updated', startWhenReady)
  ctx.effect(() => async () => {
    disposed = true
    try {
      await startup
    } finally {
      if (closeEndpoint) await closeEndpoint()
    }
  }, 'mindie-dsh-summary-host.endpoint')
  // The native loader may create this plugin before provider registrations.
  // An absent provider is not awaited here because that would block its loader.
  await startWhenReady()
}

async function openEndpoint(llm, route, endpointFile, listenPort) {
  await llm.resolveCallConfig(route)
  const token = randomBytes(32).toString('hex')
  const server = createSummaryServer({
    token,
    summarize: payload => dispatchSummaryPayload(llm, route, payload),
  })
  const dispose = async () => {
    server.closeAllConnections()
    if (server.listening) await new Promise(resolve => server.close(resolve))
    try {
      const current = JSON.parse(await readFile(endpointFile, 'utf8'))
      if (current.token === token) await unlink(endpointFile)
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
    }
  }
  const temporary = endpointFile + '.' + process.pid + '.tmp'
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject)
      server.listen(listenPort, '127.0.0.1', () => {
        server.off('error', reject)
        resolve()
      })
    })
    await mkdir(path.dirname(endpointFile), { recursive: true })
    await writeFile(temporary, JSON.stringify({
      schema: 'mindie-dsh-summary-endpoint/1', mode: 'summary-only',
      url: `http://127.0.0.1:${server.address().port}/summary`,
      token, provider: route.provider, model: route.model, pid: process.pid,
    }), { mode: 0o600 })
    await rename(temporary, endpointFile)
  } catch (error) {
    await unlink(temporary).catch(cleanup => {
      if (cleanup.code !== 'ENOENT') error.cause = cleanup
    })
    await dispose()
    throw error
  }
  return dispose
}

export default { name, inject, apply }
