'use strict'
// [Role] Resolve MindIE-owned DSH config, data, state, runtime, launch, and
// setup paths and provide the shared windowless spawn/environment boundary.
// [Invariants] Canonical `dsh.json` wins over legacy `dsh/config.json`; UTF-8
// BOM is stripped before JSON parsing; `WindowsApps` / 0-byte Python stubs are
// rejected across `dsh.json`, `MINDIE_DSH_PYTHON`, and `PATH` (`win32` never
// falls back to bare `'python'`).
// [Timeouts & Locks] Synchronous local reads only; no locks, spawns, or network.
// [Side-Effect Policy] Read-only resolution; never creates or mutates files.
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const PACKAGE_DIR = resolveCanonical(__dirname), APP = 'mindie-agent'
function env(name) { const value = process.env[name]; return typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined }
function parseJson(raw) { return JSON.parse(String(raw).replace(/^\uFEFF/, '')) }
function expandHome(raw) { return raw === '~' ? os.homedir() : raw.startsWith('~/') || raw.startsWith('~\\') ? path.join(os.homedir(), raw.slice(2)) : raw }
function stripExtendedPrefix(raw) {
  if (process.platform !== 'win32') return raw
  if (raw.startsWith('\\\\?\\UNC\\')) return '\\\\' + raw.slice(8)
  return raw.startsWith('\\\\?\\') ? raw.slice(4) : raw
}
function resolveCanonical(raw) {
  const abs = path.resolve(expandHome(raw)), tail = []
  let cur = abs
  while (true) {
    try { const real = stripExtendedPrefix(fs.realpathSync.native(cur)); return tail.length > 0 ? path.join(real, ...tail.reverse()) : real }
    catch { const parent = path.dirname(cur); if (parent === cur) return abs; tail.push(path.basename(cur)); cur = parent }
  }
}
function baseConfigHome() {
  const override = env('MINDIE_CONFIG_HOME')
  if (override) return resolveCanonical(override)
  const base = process.platform === 'win32' ? (env('APPDATA') || path.join(os.homedir(), 'AppData', 'Roaming')) : (env('XDG_CONFIG_HOME') || path.join(os.homedir(), '.config'))
  return path.join(resolveCanonical(base), APP)
}
function legacyConfigFile() {
  const dshHome = env('MINDIE_DSH_CONFIG_HOME')
  return dshHome ? path.join(resolveCanonical(dshHome), 'config.json') : path.join(baseConfigHome(), 'dsh', 'config.json')
}
function resolveConfigFile() {
  const explicit = env('MINDIE_AGENT_CONFIG') || env('MINDIE_DSH_CONFIG')
  if (explicit) {
    const expanded = expandHome(explicit)
    if (!path.isAbsolute(expanded)) throw new Error('DSH config override must be an absolute path')
    return resolveCanonical(expanded)
  }
  const canonical = path.join(baseConfigHome(), 'dsh.json')
  if (fs.existsSync(canonical)) return canonical
  const legacy = legacyConfigFile()
  return fs.existsSync(legacy) ? legacy : (env('MINDIE_DSH_CONFIG_HOME') ? legacy : canonical)
}
function configHome() { return path.dirname(resolveConfigFile()) }
function dataHome() {
  const explicit = env('MINDIE_DSH_DATA_HOME') || env('MINDIE_DATA_HOME')
  if (explicit) return resolveCanonical(explicit)
  const base = process.platform === 'win32' ? (env('LOCALAPPDATA') || path.join(os.homedir(), 'AppData', 'Local')) : (env('XDG_DATA_HOME') || path.join(os.homedir(), '.local', 'share'))
  return path.join(resolveCanonical(base), APP)
}
function paths() {
  const configFile = resolveConfigFile(), data = dataHome(), config = path.dirname(configFile)
  let installed = {}
  try { installed = parseJson(fs.readFileSync(configFile, 'utf8')) }
  catch (error) { if (error.code !== 'ENOENT') throw error }
  if (!installed || typeof installed !== 'object' || Array.isArray(installed)) throw new Error('DSH adapter configuration must be a JSON object')
  const selected = (name, fallback) => {
    const value = installed[name]
    if (value === undefined || value === null) return fallback
    if (typeof value !== 'string' || !path.isAbsolute(value)) throw new Error(`DSH adapter ${name} must be an absolute path`)
    return path.resolve(value)
  }
  const venv = selected('venv_dir', path.join(data, 'venv')), python = selected('python', path.join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'))
  const engine = env('MINDIE_DSH_ENGINE_CONFIG'), state = env('MINDIE_DSH_STATE_DIR')
  return {
    packageDir: PACKAGE_DIR, dataHome: data, configHome: config, configFile,
    engineConfig: engine ? resolveCanonical(engine) : selected('engine_config', path.join(config, 'dsh.engine.json')),
    stateDir: state ? resolveCanonical(state) : path.join(data, 'state', 'dsh'),
    venvDir: venv, venvPython: python,
    skillsDir: path.join(PACKAGE_DIR, 'skills'),
    launchScript: path.join(PACKAGE_DIR, 'scripts', 'mindie_launch.py'),
    setupScript: path.join(PACKAGE_DIR, 'scripts', 'setup.py'),
  }
}
function seedPaths() { return { seedArchive: path.join(PACKAGE_DIR, 'assets', 'mindie-harness-seed.pyz'), seedLock: path.join(PACKAGE_DIR, 'assets', 'seed-lock.json') } }
// Never detached:true on win32: libuv would create a visible console window.
function backgroundSpawnOptions(platform = process.platform) { return { windowsHide: true, detached: platform !== 'win32' } }
function buildChildEnv(baseEnv = process.env, overrides = {}, platform = process.platform) {
  const merged = {}, isWin = platform === 'win32'
  let pathValue
  for (const [key, val] of Object.entries(baseEnv || {})) {
    if (val === undefined || val === null) continue
    if (isWin && key.toUpperCase() === 'PATH') { if (pathValue === undefined || key === 'PATH') pathValue = String(val) } else merged[key] = String(val)
  }
  for (const [key, val] of Object.entries(overrides || {})) {
    if (val === undefined || val === null) continue
    if (isWin && key.toUpperCase() === 'PATH') pathValue = String(val)
    else merged[key] = String(val)
  }
  if (isWin && pathValue !== undefined) merged.PATH = pathValue
  if (merged.PYTHONUTF8 === undefined) merged.PYTHONUTF8 = '1'
  if (merged.PYTHONIOENCODING === undefined) merged.PYTHONIOENCODING = 'utf-8'
  return merged
}
function isWinStoreStub(candidate, platform = process.platform, stat = undefined) {
  if (platform !== 'win32') return false
  if (String(candidate).replace(/\//g, '\\').toLowerCase().includes('\\microsoft\\windowsapps\\')) return true
  try { return (stat || fs.statSync(candidate)).size === 0 } catch { return true }
}
function findExecutableOnPath(names, envObj = process.env, platform = process.platform) {
  const rawPath = buildChildEnv(envObj, {}, platform).PATH || ''
  const sep = platform === 'win32' ? ';' : ':'
  for (const entry of rawPath.split(sep)) {
    const dir = entry.trim().replace(/^"+|"+$/g, '')
    if (!dir) continue
    for (const name of names) {
      const candidate = path.join(dir, name)
      try {
        const stat = fs.statSync(candidate)
        if (stat.isFile() && !isWinStoreStub(candidate, platform, stat)) return candidate
      } catch {}
    }
  }
  return undefined
}
function resolveExplicitPython(raw, envObj, platform) {
  if (typeof raw !== 'string' || !raw.trim()) return undefined
  const trimmed = expandHome(raw.trim())
  if (platform !== 'win32') return (path.isAbsolute(trimmed) || trimmed.includes('/')) ? (fs.existsSync(trimmed) ? trimmed : undefined) : trimmed
  if (path.isAbsolute(trimmed) || trimmed.includes('\\') || trimmed.includes('/')) {
    try { const st = fs.statSync(trimmed); return st.isFile() && !isWinStoreStub(trimmed, 'win32', st) ? trimmed : undefined } catch { return undefined }
  }
  const names = trimmed.toLowerCase().endsWith('.exe') ? [trimmed] : [`${trimmed}.exe`, trimmed]
  return findExecutableOnPath(names, envObj, 'win32')
}
function resolvePythonCommand(binding, scriptArgs = [], envObj = process.env, platform = process.platform) {
  const venvPy = binding?.venvPython
  if (typeof venvPy === 'string' && venvPy && fs.existsSync(venvPy) && !isWinStoreStub(venvPy, platform)) {
    return { command: venvPy, args: ['-I', '-X', 'utf8', ...scriptArgs] }
  }
  const explicit = resolveExplicitPython(envObj?.MINDIE_DSH_PYTHON, envObj, platform)
  if (explicit) return { command: explicit, args: ['-I', '-X', 'utf8', ...scriptArgs] }
  const uvExe = findExecutableOnPath(platform === 'win32' ? ['uv.exe'] : ['uv'], envObj, platform)
  if (uvExe) return { command: uvExe, args: ['run', '--no-project', '--python', '>=3.11,<3.15', 'python', '-B', '-X', 'utf8', ...scriptArgs] }
  const pyExe = findExecutableOnPath(platform === 'win32' ? ['python.exe', 'python3.exe'] : ['python3', 'python'], envObj, platform)
  if (pyExe) return { command: pyExe, args: ['-I', '-X', 'utf8', ...scriptArgs] }
  if (platform === 'win32') throw new Error('No valid Python >=3.11 or uv launcher found on Windows PATH (WindowsApps stubs excluded)')
  return { command: 'python3', args: ['-I', '-X', 'utf8', ...scriptArgs] }
}
module.exports = { paths, seedPaths, dataHome, configHome, backgroundSpawnOptions, buildChildEnv, isWinStoreStub, resolvePythonCommand }
