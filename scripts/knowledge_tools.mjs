// [Role] Shared DSH knowledge-tool parameter schemas and the bounded C6 RPC budget.
// [Invariants] Distillation accepts only selected evidence inputs, an explicit
// task_state and an optional configured domain; no session/activation identity
// fields. The schema travels to the Harness as prompt text because
// GenerateOptions has no constrained-JSON field.
// [Timeouts & Locks] Mirrors the shared C6 CLI budget (two redaction scans,
// identity, summary worker, 35s receipt/cleanup, 30s transport); the Python
// sidecar owns the exact inner budget and enforces it.
// [Side-Effect Policy] Pure constants and environment reads; no I/O.

const SUMMARY_WORKER_DEFAULT_SECONDS = 315
const SUMMARY_IDENTITY_SECONDS = 10
const DISTILL_RECEIPT_SECONDS = 35
const REDACTION_DEFAULT_SECONDS = 30
const DISTILL_TRANSPORT_SECONDS = 30

export const knowledgeDomainProperty = {
  type: 'string',
  pattern: '^[a-z][a-z0-9-]{0,63}$',
  description: 'Optional configured knowledge domain; omit to use the default domain.',
}

export const knowledgeQueryExtraProperties = {
  domain: knowledgeDomainProperty,
  domain_path: { type: 'string' },
  token_budget: { type: 'integer', minimum: 1 },
  include_unknown: { type: 'boolean' },
  include_archived: { type: 'boolean' },
  navigation_only: { type: 'boolean' },
  run_id: { type: 'string', maxLength: 256 },
}

export const knowledgeExplainParameters = {
  type: 'object',
  properties: {
    ref: { type: 'string' },
    refs: { type: 'array', items: { type: 'string' } },
    conditions: { type: 'object' },
    known_refs: { type: 'array', items: { type: 'string' } },
    token_budget: { type: 'integer', minimum: 1, maximum: 16000 },
    run_id: { type: 'string', maxLength: 256 },
  },
  required: [],
  additionalProperties: false,
}

export const knowledgeDistillParameters = {
  type: 'object',
  properties: {
    domain: knowledgeDomainProperty,
    related_refs: { type: 'array', items: { type: 'string' } },
    proposal: { type: 'object' },
    inputs: {
      type: 'array',
      minItems: 1,
      items: {
        type: 'object',
        properties: {
          kind: { type: 'string' },
          outcome: { type: 'string' },
          source_locator: { type: 'string' },
          content: { type: 'string' },
          evidence_refs: { type: 'array', items: { type: 'string' } },
          repetitions: { type: 'integer', minimum: 1 },
        },
        required: ['kind', 'outcome', 'source_locator', 'content', 'evidence_refs'],
        additionalProperties: false,
      },
    },
    task_state: {
      type: 'string',
      enum: ['in_flight', 'verified_milestone', 'completed'],
    },
  },
  required: ['inputs', 'task_state'],
  additionalProperties: false,
}

export function distillCallTimeoutMs() {
  const worker = Number(process.env.MINDIE_SUMMARY_TIMEOUT_SECONDS)
  const scanner = Number(process.env.MINDIE_REDACTION_TIMEOUT_SECONDS)
  const workerSeconds = Number.isFinite(worker) && worker > 5 ? worker : SUMMARY_WORKER_DEFAULT_SECONDS
  const scannerSeconds = Number.isFinite(scanner) && scanner > 0 ? scanner : REDACTION_DEFAULT_SECONDS
  return Math.ceil(
    (2 * scannerSeconds + SUMMARY_IDENTITY_SECONDS + workerSeconds
      + DISTILL_RECEIPT_SECONDS + DISTILL_TRANSPORT_SECONDS) * 1000,
  )
}

export const knowledgeDistillDescription =
  'Distill explicitly selected, already-sanitized evidence through the shared C6 ' +
  'graded-knowledge pipeline. Requires an activated task and an explicit ' +
  'verified milestone/completion supplied by the caller; an ordinary Stop is not ' +
  'completion. Pass optional `related_refs` to compare existing units for an ' +
  'incremental revision, or a foreground `proposal` (mindie-distill-decision/1) ' +
  'for deterministic validation without a model call. Only selected inputs, ' +
  'task_state, related_refs, proposal and an optional configured domain are ' +
  'accepted; activation comes from the native binding. Requires a live DSH host ' +
  'summary endpoint and a paired runtime with C6 support; otherwise unavailable.'

export function registerKnowledgeDistill(ctx, {
  tool, sidecar, requireState, text, timeoutMs = distillCallTimeoutMs,
}) {
  ctx.tools.register(tool(
    'knowledge_distill',
    knowledgeDistillDescription,
    knowledgeDistillParameters,
    async (args, exec) => {
      const { session, state } = requireState(exec)
      const { _tool_error: isError, ...result } = await sidecar.call(
        'rpc',
        { session: session.id, token: state.token, method: 'distill', arguments: args },
        exec.signal,
        timeoutMs(),
      )
      if (isError) throw new Error(text(result))
      return text(result)
    },
  ))
}
