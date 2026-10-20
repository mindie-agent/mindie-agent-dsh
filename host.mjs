// Stable Host launcher for mindie-agent-dsh.
//
// [Role] Mounts the committed `host3.mjs` generation and hot-swaps it when
// `current.json` changes, without restarting DSH.
// [Invariants] A missing/corrupt generation pointer falls back to the bundled
// seed and never leaves the previous fiber unmounted.
// [Timeouts & Locks] One 5s pointer poll; no network/model/child work.
// [Side-Effect Policy] Reads `current.json`; mounts/disposes Cordis fibers and
// writes only the launcher log.
//
// The profile points at this file. The updater writes an immutable generation
// pointer; this launcher mounts the generation as a child Cordis plugin and
// swaps it when the pointer changes, so an update does not require replacing
// the loaded bundle or restarting DSH. The bundled host3.mjs is the seed.

import { createRequire } from 'node:module'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const require = createRequire(import.meta.url)
const paths = require('./paths.cjs').paths()
const logPath = path.join(paths.dataHome, 'update', 'launcher.log')
const log = (message) => { try { appendFileSync(logPath, new Date().toISOString() + ' ' + message + '\n') } catch {} }

export const name = 'mindie-agent-dsh'
export const inject = ['tools', 'agents', 'llm']

function pointerPath() {
  return path.join(paths.dataHome, 'update', 'current.json')
}

function entryFor() {
  try {
    const pointer = JSON.parse(readFileSync(pointerPath(), 'utf8'))
    if (typeof pointer?.generation === 'string' && pointer.generation) {
      const candidate = path.join(pointer.generation, 'host3.mjs')
      if (existsSync(candidate)) return candidate
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      log('generation pointer unreadable: ' + String(error?.message || error))
    }
  }
  return path.join(paths.packageDir, 'host3.mjs')
}

async function mount(ctx, entry) {
  const module = await import(pathToFileURL(entry).href)
  const plugin = module.default && typeof module.default.apply === 'function' ? module.default : module
  if (!plugin || typeof plugin.apply !== 'function') {
    throw new Error('generation has no apply function: ' + entry)
  }
  const fiber = await ctx.plugin(plugin)
  log('mounted ' + entry)
  return { fiber, entry }
}

export async function apply(ctx) {
  log('launcher start seed=' + entryFor())
  let current = await mount(ctx, entryFor())
  let timer = setInterval(async () => {
    const entry = entryFor()
    if (entry === current.entry) return
    const previous = current
    try {
      await previous.fiber?.dispose?.()
      current = await mount(ctx, entry)
    } catch (error) {
      log('mount failed ' + entry + ' ' + String(error?.message || error))
      try {
        current = await mount(ctx, previous.entry)
      } catch (rollbackError) {
        log('rollback failed ' + String(rollbackError?.message || rollbackError))
      }
    }
  }, 5000)
  timer.unref?.()
  ctx.effect(() => () => {
    clearInterval(timer)
    current?.fiber?.dispose?.().catch(() => {})
  }, 'mindie-agent-dsh.launcher')
}

export default { name, inject, apply }
