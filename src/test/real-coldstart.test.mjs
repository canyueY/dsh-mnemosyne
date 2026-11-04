/**
 * Real DSH 0.2.1-rc.5 cold-start boundary.
 *
 * This is intentionally opt-in: it needs a DSH CLI and an isolated profile
 * prepared by the verification script, because a normal unit test must not
 * mutate the user's DSH_HOME. It boots the real web app twice and reads the
 * probe written by the coldstart-probe plugin after `settings.describe()`.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { setTimeout as delay } from 'node:timers/promises'

const cli = process.env.COLDSTART_DSH_CLI
const home = process.env.COLDSTART_DSH_HOME
const profile = process.env.COLDSTART_DSH_PROFILE || 'mnemo-test'
const probeDir = process.env.COLDSTART_PROBE_DIR
const enabled = process.platform === 'win32' && Boolean(cli && home && probeDir)

async function boot(port, probeFile) {
  rmSync(probeFile, { force: true })
  const child = spawn(`"${cli}" --profile ${profile} --no-open --port ${port}`, {
    shell: true,
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
    env: { ...process.env, DSH_HOME: home, PROBE_OUT: probeFile },
  })
  try {
    for (let i = 0; i < 80; i++) {
      if (existsSync(probeFile)) break
      await delay(250)
    }
    assert.ok(existsSync(probeFile), `probe not written on port ${port}`)
    return JSON.parse(readFileSync(probeFile, 'utf8'))
  } finally {
    await new Promise((resolve) => {
      const p = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
      p.on('close', resolve)
      p.on('error', resolve)
    })
  }
}

test('real rc.5 cold start: settings.describe twice, tools register, Config is absent', { skip: !enabled }, async () => {
  const first = await boot(40601, `${probeDir}/probe-test-1.json`)
  const second = await boot(40602, `${probeDir}/probe-test-2.json`)
  for (const result of [first, second]) {
    assert.equal(result.ok, true, result.error || 'probe failed')
    assert.ok(result.count >= 1)
    assert.ok(result.memoryTools.includes('memory_health'))
    assert.equal(result.rowConfig?.auto_start, true)
  }
})
