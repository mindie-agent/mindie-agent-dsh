'use strict'

// MindIE-owned DSH paths. The bundle patch and the Python scripts resolve the
// same locations; tests assert JavaScript/Python agreement.
//
// Environment overrides are intentionally MINDIE_* rather than DSH_*: the DSH
// MCP stdio transport scrubs credential-shaped and stale DSH_* names from the
// child environment.

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const PACKAGE_DIR = __dirname
const APP = path.join('mindie-agent', 'dsh')

function env(name) {
  const value = process.env[name]
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined
}

function posixDataBase() {
  return env('XDG_DATA_HOME') || path.join(os.homedir(), '.local', 'share')
}

function posixConfigBase() {
  return env('XDG_CONFIG_HOME') || path.join(os.homedir(), '.config')
}

function dataHome() {
  const override = env('MINDIE_DSH_DATA_HOME')
  if (override) return path.resolve(override)
  if (process.platform === 'win32') {
    return path.join(env('LOCALAPPDATA') || path.join(os.homedir(), 'AppData', 'Local'), APP)
  }
  return path.join(posixDataBase(), APP)
}

function configHome() {
  const override = env('MINDIE_DSH_CONFIG_HOME')
  if (override) return path.resolve(override)
  if (process.platform === 'win32') {
    return path.join(env('APPDATA') || path.join(os.homedir(), 'AppData', 'Roaming'), APP)
  }
  return path.join(posixConfigBase(), APP)
}

function paths() {
  const data = dataHome()
  const config = configHome()
  const configFile = env('MINDIE_DSH_CONFIG') || path.join(config, 'config.json')
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
  return {
    packageDir: PACKAGE_DIR,
    dataHome: data,
    configHome: config,
    configFile,
    engineConfig: env('MINDIE_DSH_ENGINE_CONFIG') || selected('engine_config', path.join(config, 'engine.json')),
    stateDir: env('MINDIE_DSH_STATE_DIR') || path.join(data, 'state'),
    venvDir: venv,
    venvPython: python,
    skillsDir: path.join(PACKAGE_DIR, 'skills'),
    knowledgeScript: path.join(PACKAGE_DIR, 'scripts', 'knowledge_mcp.py'),
    setupScript: path.join(PACKAGE_DIR, 'scripts', 'setup.py'),
  }
}

module.exports = { paths, dataHome, configHome }
