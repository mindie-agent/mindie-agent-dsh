import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  MAX_LOG_BYTES,
  appendBoundedLine,
  rotateIfNeeded,
} from '../scripts/log_rotate.mjs'

function size(target) {
  try {
    return statSync(target).size
  } catch {
    return 0
  }
}

test('launcher/host logs rotate at 2 MiB and stay bounded', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-log-rotate-'))
  const active = path.join(root, 'host.log')
  const rotated = active + '.1'
  try {
    const line = 'x'.repeat(60 * 1024)
    for (let index = 0; index < 40; index += 1) {
      assert.equal(appendBoundedLine(active, line), true)
    }
    assert.ok(size(rotated) > 0, 'the first rotation must create the .1 sibling')
    assert.ok(size(active) < MAX_LOG_BYTES, 'the active file must be below the cap')
    assert.ok(size(active) + size(rotated) <= 4 * 1024 * 1024 + 64 * 1024,
      'active + .1 must retain at most two bounded generations')
    assert.equal(rotateIfNeeded(active), false)
    assert.equal(size(rotated), statSync(rotated).size)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('a blocked .1 rename is tolerated on Windows-like filesystems', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'mindie-log-blocked-'))
  const active = path.join(root, 'launcher.log')
  const rotated = active + '.1'
  try {
    mkdirSync(rotated)
    const line = 'y'.repeat(60 * 1024)
    for (let index = 0; index < 40; index += 1) {
      assert.equal(appendBoundedLine(active, line), true)
    }
    assert.ok(size(active) >= MAX_LOG_BYTES,
      'a failed rename keeps appending to the active bounded file')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
