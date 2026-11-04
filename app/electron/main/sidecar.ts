/**
 * Python sidecar lifecycle (PLAN §6): random token + free port, spawn via uv, wait for
 * `READY <port>`, restart on crash with backoff, tree-kill on quit.
 */
import { type ChildProcess, spawn, spawnSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { createWriteStream, mkdirSync, type WriteStream } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'
import { app } from 'electron'
import type { BackendInfo } from '../../src/shared/bridge'

const READY_TIMEOUT_MS = 15_000
const RESTART_BACKOFF_MS = [1_000, 2_000, 5_000]
/** A sidecar that stayed up this long resets the backoff ladder. */
const STABLE_AFTER_MS = 30_000

export interface SidecarEvents {
  ready: [BackendInfo]
  'sidecar-failed': [Error]
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.unref()
    srv.once('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      srv.close(() => (port ? resolve(port) : reject(new Error('no port assigned'))))
    })
  })
}

function treeKill(child: ChildProcess): void {
  if (child.pid === undefined || child.exitCode !== null) return
  if (process.platform === 'win32') {
    // `uv run` spawns python as a child; /T takes the whole tree down.
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true })
  } else {
    child.kill('SIGTERM')
  }
}

export class Sidecar extends EventEmitter<SidecarEvents> {
  private readonly token = randomBytes(32).toString('hex')
  private child: ChildProcess | null = null
  private info: BackendInfo | null = null
  private failure: Error | null = null
  private waiters: { resolve: (i: BackendInfo) => void; reject: (e: Error) => void }[] = []
  private attempt = 0
  private stopping = false
  private restartTimer: NodeJS.Timeout | null = null
  private log: WriteStream | null = null

  constructor(
    private readonly coreDir: string,
    private readonly logDir: string,
    /** Extra Origin the server should accept (the dev renderer URL). */
    private readonly devOrigin?: string,
  ) {
    super()
  }

  /** Resolves with the current backend once READY; waits across restarts. */
  backend(): Promise<BackendInfo> {
    if (this.info) return Promise.resolve(this.info)
    if (this.failure) return Promise.reject(this.failure)
    return new Promise((resolve, reject) => this.waiters.push({ resolve, reject }))
  }

  start(): void {
    mkdirSync(this.logDir, { recursive: true })
    this.log = createWriteStream(join(this.logDir, 'sidecar.log'), { flags: 'a' })
    void this.launch()
  }

  stop(): void {
    this.stopping = true
    if (this.restartTimer) clearTimeout(this.restartTimer)
    if (this.child) treeKill(this.child)
    this.child = null
    this.log?.end()
  }

  private writeLog(line: string): void {
    this.log?.write(`${new Date().toISOString()} ${line}\n`)
  }

  private async launch(): Promise<void> {
    if (this.stopping) return
    this.info = null
    let port: number
    try {
      port = await freePort()
    } catch (err) {
      this.onExit(new Error(`could not allocate a port: ${String(err)}`), Date.now())
      return
    }

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      // Never on the command line — other processes can read argv (§6.2).
      CLICKACHU_TOKEN: this.token,
      PYTHONUNBUFFERED: '1',
    }
    if (this.devOrigin) env['CLICKACHU_ALLOWED_ORIGINS'] = this.devOrigin

    const args = [
      'run',
      '--project',
      this.coreDir,
      'python',
      '-m',
      'clickachu',
      '--port',
      String(port),
    ]
    this.writeLog(`spawn uv ${args.join(' ')}`)
    const child = spawn('uv', args, { cwd: this.coreDir, env, windowsHide: true })
    this.child = child
    const startedAt = Date.now()
    let settled = false

    const readyTimer = setTimeout(() => {
      if (settled) return
      settled = true
      this.writeLog(`no READY within ${READY_TIMEOUT_MS} ms; killing`)
      treeKill(child)
    }, READY_TIMEOUT_MS)

    let stdoutBuf = ''
    child.stdout?.setEncoding('utf8')
    child.stdout?.on('data', (chunk: string) => {
      this.log?.write(chunk)
      stdoutBuf += chunk
      let nl: number
      while ((nl = stdoutBuf.indexOf('\n')) >= 0) {
        const line = stdoutBuf.slice(0, nl).trim()
        stdoutBuf = stdoutBuf.slice(nl + 1)
        const m = /^READY (\d+)$/.exec(line)
        if (m && !settled && Number(m[1]) === port) {
          settled = true
          clearTimeout(readyTimer)
          this.onReady({ baseUrl: `http://127.0.0.1:${port}`, token: this.token })
        }
      }
    })
    child.stderr?.setEncoding('utf8')
    child.stderr?.on('data', (chunk: string) => this.log?.write(chunk))

    // 'exit' doesn't fire when spawning itself fails (e.g. uv not on PATH), so both paths end here.
    let ended = false
    const end = (err: Error): void => {
      if (ended) return
      ended = true
      clearTimeout(readyTimer)
      settled = true
      if (this.child === child) this.child = null
      this.writeLog(err.message)
      this.onExit(err, startedAt)
    }
    child.once('error', (err) => end(new Error(`sidecar spawn error: ${err.message}`)))
    child.once('exit', (code, signal) => end(new Error(`sidecar exited (${code ?? signal})`)))
  }

  private onReady(info: BackendInfo): void {
    this.writeLog(`READY ${info.baseUrl}`)
    this.info = info
    this.failure = null
    for (const w of this.waiters.splice(0)) w.resolve(info)
    this.emit('ready', info)
  }

  private onExit(err: Error, startedAt: number): void {
    this.info = null
    if (this.stopping) return
    if (Date.now() - startedAt >= STABLE_AFTER_MS) this.attempt = 0
    const delay = RESTART_BACKOFF_MS[this.attempt]
    if (delay === undefined) {
      this.failure = new Error(
        `${err.message}; gave up after ${RESTART_BACKOFF_MS.length} restarts`,
      )
      this.writeLog(this.failure.message)
      for (const w of this.waiters.splice(0)) w.reject(this.failure)
      this.emit('sidecar-failed', this.failure)
      return
    }
    this.attempt += 1
    this.writeLog(`restarting in ${delay} ms (attempt ${this.attempt})`)
    this.restartTimer = setTimeout(() => void this.launch(), delay)
  }
}

/**
 * Dev: the repo's core/ next to app/. TODO(packaging): bundle the Python env and resolve it
 * from process.resourcesPath when app.isPackaged.
 */
export function defaultCoreDir(): string {
  return join(app.getAppPath(), '..', 'core')
}

export function defaultLogDir(): string {
  return join(app.getPath('appData'), 'Clickachu', 'logs')
}
