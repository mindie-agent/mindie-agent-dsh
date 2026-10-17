import assert from 'node:assert/strict'
import { summarizeWithLlm } from '../host3.mjs'

const request = {
  task_id: 'a'.repeat(64),
  body_version: 'rev-1',
  batch_id: 'b'.repeat(64),
  input_digest: 'c'.repeat(64),
  policy_identity: { fingerprint: 'd'.repeat(64) },
  blocks: [{ block_id: 'e'.repeat(64), text: 'hello' }],
}

const llm = {
  async *stream(options) {
    assert.equal(options.provider, 'deepseek-official')
    assert.equal(options.model, 'deepseek-flash')
    yield { type: 'text-delta', index: 0, text: '{"blocks":' }
    yield { type: 'text-delta', index: 0, text: '[]}' }
    yield { type: 'usage', usage: { inputTokens: 12, outputTokens: 34, cacheReadTokens: 5 } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}

const result = await summarizeWithLlm(
  llm,
  { provider: 'deepseek-official', model: 'deepseek-flash' },
  request,
  'index these blocks',
)
assert.equal(result.text, '{"blocks":[]}')
assert.deepEqual(result.usage, { input_tokens: 12, cached_input_tokens: 5, output_tokens: 34 })

await assert.rejects(
  () => summarizeWithLlm(llm, undefined, request, 'prompt'),
  (error) => error.statusCode === 409,
)

const failing = {
  async *stream() {
    yield { type: 'finish', reason: { kind: 'error', failure: { code: 'AUTH' } } }
  },
}
await assert.rejects(
  () => summarizeWithLlm(failing, { provider: 'p', model: 'm' }, request, 'prompt'),
  /model finish: AUTH/,
)

console.log('summary endpoint unit tests passed')
