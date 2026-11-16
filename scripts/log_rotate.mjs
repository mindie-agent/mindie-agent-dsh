// [Role] Bounded append-only log rotation shared by the DSH launcher and host.
// [Invariants] One active file plus at most one `.1` sibling; a single file
// rotates at 2 MiB, so steady-state retention stays at or below ~4 MiB. A
// Windows rename/unlink conflict is tolerated and the append still succeeds.
// [Timeouts & Locks] Synchronous `stat`/`rename`/`append` only; no lock is
// acquired and no write waits for another process.
// [Side-Effect Policy] Appends to the caller's log and renames/removes only
// that log's own `.1` sibling; never reads other payloads or writes elsewhere.

import { appendFileSync, renameSync, statSync, unlinkSync } from 'node:fs'

export const MAX_LOG_BYTES = 2 * 1024 * 1024
export const MAX_LOG_LINE_BYTES = 64 * 1024

function sizeOf(target) {
  try {
    return statSync(target).size
  } catch {
    return 0
  }
}

export function rotateIfNeeded(target) {
  if (sizeOf(target) < MAX_LOG_BYTES) return false
  const rotated = target + '.1'
  try {
    unlinkSync(rotated)
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      // A locked/foreign `.1` is not fatal: keep appending to the active file.
    }
  }
  try {
    renameSync(target, rotated)
    return true
  } catch {
    // Windows may still hold the old file or the target entry; retry next time.
    return false
  }
}

export function appendBoundedLine(target, line) {
  let body = String(line ?? '')
  if (body.length > MAX_LOG_LINE_BYTES) body = body.slice(0, MAX_LOG_LINE_BYTES)
  try {
    appendFileSync(target, body.endsWith('\n') ? body : body + '\n')
    if (sizeOf(target) >= MAX_LOG_BYTES && rotateIfNeeded(target)) {
      try { appendFileSync(target, '') } catch {}
    }
    return true
  } catch {
    return false
  }
}

export default { MAX_LOG_BYTES, appendBoundedLine, rotateIfNeeded }
