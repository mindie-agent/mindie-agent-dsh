import assert from 'node:assert/strict'
import { dispatchSummaryPayload, summarizeWithLlm } from '../host3.mjs'

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

const missingUsage = {
  async *stream() {
    yield { type: 'text-delta', index: 0, text: '{"blocks":[]}' }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
const unmetered = await summarizeWithLlm(
  missingUsage, { provider: 'p', model: 'm' }, request, 'prompt',
)
assert.equal(unmetered.text, '{"blocks":[]}')
assert.equal(unmetered.usage, null)

// The installed DSH pi-ai adapter emits this shape for a zero-cache call.
const zeroCacheUsage = {
  async *stream() {
    yield { type: 'text-delta', index: 0, text: '{"blocks":[]}' }
    yield { type: 'usage', usage: { inputTokens: 12, outputTokens: 34 } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
const zeroCacheMetered = await summarizeWithLlm(
  zeroCacheUsage, { provider: 'p', model: 'm' }, request, 'prompt',
)
assert.deepEqual(zeroCacheMetered.usage, { input_tokens: 12, cached_input_tokens: 0, output_tokens: 34 })

const createdCacheUsage = {
  async *stream() {
    yield { type: 'text-delta', index: 0, text: '{"blocks":[]}' }
    yield { type: 'usage', usage: {
      inputTokens: 12, outputTokens: 34, cacheReadTokens: 5, cacheWriteTokens: 8,
    } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
const cacheMetered = await summarizeWithLlm(
  createdCacheUsage, { provider: 'p', model: 'm' }, request, 'prompt',
)
assert.deepEqual(cacheMetered.usage, { input_tokens: 20, cached_input_tokens: 5, output_tokens: 34 })

const partialUsage = {
  async *stream() {
    yield { type: 'text-delta', index: 0, text: '{"blocks":[]}' }
    yield { type: 'usage', usage: { inputTokens: 12 } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
const partiallyMetered = await summarizeWithLlm(
  partialUsage, { provider: 'p', model: 'm' }, request, 'prompt',
)
assert.equal(partiallyMetered.usage, null)

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

// C6 routing: the original prompt carries the shared decision schema in text;
// the SDK has no constrained-JSON option, so llm.stream receives no schema field.
let distillOptions
const distillLlm = {
  async *stream(options) {
    distillOptions = options
    yield { type: 'text-delta', index: 0, text: '{"schema_version":"mindie-distill-decision/1"}' }
    yield { type: 'usage', usage: { inputTokens: 1, cacheReadTokens: 2, outputTokens: 3 } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
const distillResult = await dispatchSummaryPayload(
  distillLlm,
  { provider: 'p', model: 'm' },
  {
    schema: 'mindie-distill-request/1',
    request: {
      schema_version: 'mindie-distill-request/1', sanitized_inputs: [],
      policy_identity: { model: 'm', implementation: { provider: 'p' } },
    },
    prompt: 'shared distill prompt',
    output_schema: { type: 'object', properties: { action: { type: 'string' } } },
  },
)
assert.equal(distillResult.text, '{"schema_version":"mindie-distill-decision/1"}')
const distillText = distillOptions.messages[0].content[0].text
assert.match(distillText, /shared distill prompt/)
assert.match(distillText, /"action"/)
assert.doesNotMatch(distillText, /Use the key name "blocks"/)
assert.equal(Object.hasOwn(distillOptions, 'schema'), false)
assert.equal(Object.hasOwn(distillOptions, 'responseFormat'), false)

const previousOptions = distillOptions
assert.throws(
  () => dispatchSummaryPayload(distillLlm, { provider: 'p', model: 'changed-model' }, {
    schema: 'mindie-distill-request/1', prompt: 'shared prompt', output_schema: { type: 'object' },
    request: { policy_identity: { model: 'm', implementation: { provider: 'p' } } },
  }),
  (error) => error.statusCode === 409 && /changed after admission/.test(error.message),
)
assert.equal(distillOptions, previousOptions)

let legacyOptions
const legacyLlm = {
  async *stream(options) {
    legacyOptions = options
    yield { type: 'text-delta', index: 0, text: '{"blocks":[],"navigation":{"title":"T","summary":"S"}}' }
    yield { type: 'finish', reason: { kind: 'stop' } }
  },
}
await dispatchSummaryPayload(legacyLlm, { provider: 'p', model: 'm' }, {
  request: { task_id: 'a'.repeat(64) },
  prompt: 'legacy prompt',
})
assert.match(legacyOptions.messages[0].content[0].text, /Use the key name "blocks"/)
assert.doesNotMatch(legacyOptions.messages[0].content[0].text, /mindie-distill-decision\/1/)

assert.throws(
  () => dispatchSummaryPayload(distillLlm, { provider: 'p', model: 'm' }, {
    schema: 'mindie-distill-request/2',
    prompt: 'p',
    request: {},
  }),
  (error) => error.statusCode === 400 && /unsupported summary payload schema/.test(error.message),
)
assert.throws(
  () => dispatchSummaryPayload(distillLlm, { provider: 'p', model: 'm' }, {
    schema: 'mindie-distill-request/1',
    prompt: 'p',
    request: {},
  }),
  (error) => error.statusCode === 400 && /invalid distillation payload/.test(error.message),
)

console.log('summary endpoint unit tests passed')
