'use strict'

// [Role] Resolves MindIE-owned DSH data, config, state, and runtime paths.
// [Invariants] JavaScript and Python (`mindie_paths.py`) resolve identical
// paths; adapter overrides in `config.json` must be absolute paths.
// [Timeouts & Locks] Synchronous local environment and `config.json` read; no
// locks, child processes, or network I/O.
// [Side-Effect Policy] Read-only path resolution and `config.json` inspection;
// never creates or mutates files.
//
// MindIE-owned DSH paths. The bundle patch and the Python scripts resolve the
// same locations; tests assert JavaScript/Python agreement.
//
// Environment overrides are intentionally MINDIE_* rather than DSH_*: the DSH
// MCP stdio transport scrubs credential-shaped and stale DSH_* names from the
// child environment.

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

function stripExtendedPrefix(raw) {
  if (process.platform !== 'win32' || typeof raw !== 'string') return raw
  if (raw.startsWith('\\\\?\\UNC\\')) return '\\\\' + raw.slice(8)
  if (raw.startsWith('\\\\?\\')) return raw.slice(4)
  return raw
}

function expandHome(raw) {
  if (raw === '~') return os.homedir()
  if (raw.startsWith('~/') || raw.startsWith('~\\')) {
    return path.join(os.homedir(), raw.slice(2))
  }
  return raw
}

function resolveCanonical(raw) {
  const abs = path.resolve(expandHome(raw))
  const tail = []
  let cur = abs
  while (true) {
    try {
      const real = stripExtendedPrefix(fs.realpathSync.native(cur))
      return tail.length > 0 ? path.join(real, ...tail.reverse()) : real
    } catch {
      const parent = path.dirname(cur)
      if (parent === cur) return abs
      tail.push(path.basename(cur))
      cur = parent
    }
  }
}

const PACKAGE_DIR = resolveCanonical(__dirname)
const APP = path.join('mindie-agent', 'dsh')

function env(name) {
  const value = process.env[name]
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined
}

function posixDataBase() {
  return expandHome(env('XDG_DATA_HOME') || path.join(os.homedir(), '.local', 'share'))
}

function posixConfigBase() {
  return expandHome(env('XDG_CONFIG_HOME') || path.join(os.homedir(), '.config'))
}

function dataHome() {
  const override = env('MINDIE_DSH_DATA_HOME')
  if (override) return resolveCanonical(override)
  if (process.platform === 'win32') {
    return path.join(expandHome(env('LOCALAPPDATA') || path.join(os.homedir(), 'AppData', 'Local')), APP)
  }
  return path.join(posixDataBase(), APP)
}

function configHome() {
  const override = env('MINDIE_DSH_CONFIG_HOME')
  if (override) return resolveCanonical(override)
  if (process.platform === 'win32') {
    return path.join(expandHome(env('APPDATA') || path.join(os.homedir(), 'AppData', 'Roaming')), APP)
  }
  return path.join(posixConfigBase(), APP)
}

function paths() {
  const data = dataHome()
  const config = configHome()
  const cfgOverride = env('MINDIE_DSH_CONFIG')
  const configFile = cfgOverride ? resolveCanonical(cfgOverride) : path.join(config, 'config.json')
  let installed = {}
  try {
    installed = JSON.parse(fs.readFileSync(configFile, 'utf8'))
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  if (!installed || typeof installed !== 'object' || Array.isArray(installed)) {
    throw new Error('DSH adapter configuration must be an object')
  }
  const selected = (name, fallback) => {
    const value = installed[name]
    if (value === undefined || value === null) return fallback
    if (typeof value !== 'string' || !path.isAbsolute(value)) {
      throw new Error(`DSH adapter ${name} must be an absolute path`)
    }
    return value
  }
  const venv = selected('venv_dir', path.join(data, 'venv'))
  const python = selected('python', path.join(
    venv,
    process.platform === 'win32' ? path.join('Scripts', 'python.exe') : path.join('bin', 'python'),
  ))
  const engOverride = env('MINDIE_DSH_ENGINE_CONFIG')
  const stateOverride = env('MINDIE_DSH_STATE_DIR')
  return {
    packageDir: PACKAGE_DIR,
    dataHome: data,
    configHome: config,
    configFile,
    engineConfig: engOverride ? resolveCanonical(engOverride) : selected('engine_config', path.join(config, 'engine.json')),
    stateDir: stateOverride ? resolveCanonical(stateOverride) : path.join(data, 'state'),
    venvDir: venv,
    venvPython: python,
    skillsDir: path.join(PACKAGE_DIR, 'skills'),
    knowledgeScript: path.join(PACKAGE_DIR, 'scripts', 'knowledge_mcp.py'),
    setupScript: path.join(PACKAGE_DIR, 'scripts', 'setup.py'),
  }
}

module.exports = { paths, dataHome, configHome }
