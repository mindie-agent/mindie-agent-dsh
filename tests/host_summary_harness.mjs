// Test-only native-boundary stub for host3.mjs's actual summary handler.
//
// This does not implement a second summary server: the HTTP handler and payload
// routing come from the production `createSummaryServer`/`dispatchSummaryPayload`.
// Only the DSH LLM boundary (`llm.stream`) is stubbed, so the integration test
// exercises organizer payload -> host handler -> model stub -> shared outcome.

import { writeFileSync } from 'node:fs'
import { createSummaryServer, dispatchSummaryPayload } from '../host3.mjs'

const endpointPath = process.env.DISTILL_STUB_ENDPOINT
const capturePath = process.env.DISTILL_STUB_CAPTURE
const token = process.env.DISTILL_STUB_TOKEN || 'distill-stub-token'
const text = process.env.DISTILL_STUB_TEXT || ''
const usage = process.env.DISTILL_STUB_USAGE ? JSON.parse(process.env.DISTILL_STUB_USAGE) : undefined
const mode = process.env.DISTILL_STUB_MODE || 'ok'

const llm = {
  async *stream(options) {
    if (capturePath) {
      writeFileSync(capturePath, JSON.stringify({
        optionKeys: Object.keys(options),
        messages: options.messages,
      }))
    }
    if (mode === 'error') throw new Error('stub model failed after send')
    if (text) yield { type: 'text-delta', index: 0, text }
    if (mode !== 'no-usage' && usage) yield { type: 'usage', usage }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}

const server = createSummaryServer({
  token,
  summarize: (payload) => dispatchSummaryPayload(
    llm, { provider: 'stub-provider', model: 'stub-model' }, payload,
  ),
})

server.listen(0, '127.0.0.1', () => {
  if (!endpointPath) throw new Error('DISTILL_STUB_ENDPOINT is required')
  writeFileSync(endpointPath, JSON.stringify({
    url: `http://127.0.0.1:${server.address().port}/summary`,
    token,
  }))
})

const close = () => {
  server.close(() => process.exit(0))
}
process.on('SIGTERM', close)
process.on('SIGINT', close)
