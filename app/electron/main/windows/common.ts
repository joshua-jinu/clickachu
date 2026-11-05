/** Shared window plumbing: hardened webPreferences (§6.8), renderer page loading, external links. */
import { join } from 'node:path'
import { app, type BrowserWindow, shell, type WebPreferences } from 'electron'

export type RendererPage = 'orb' | 'quickbar' | 'panel'

const positionAnimations = new Map<BrowserWindow, ReturnType<typeof setTimeout>>()

/** Ease a native window between positions without changing its dimensions. */
export function animateWindowPosition(win: BrowserWindow, x: number, y: number): void {
  const previousTimer = positionAnimations.get(win)
  if (previousTimer) clearTimeout(previousTimer)
  positionAnimations.delete(win)
  if (win.isDestroyed()) return

  const start = win.getBounds()
  if (start.x === x && start.y === y) return
  const startedAt = Date.now()
  const duration = 180

  const step = (): void => {
    if (win.isDestroyed()) {
      positionAnimations.delete(win)
      return
    }
    const progress = Math.min(1, (Date.now() - startedAt) / duration)
    const eased = progress * progress * (3 - 2 * progress)
    win.setPosition(
      Math.round(start.x + (x - start.x) * eased),
      Math.round(start.y + (y - start.y) * eased),
    )
    if (progress < 1) {
      positionAnimations.set(win, setTimeout(step, 16))
    } else {
      positionAnimations.delete(win)
    }
  }

  positionAnimations.set(win, setTimeout(step, 16))
}

const devUrl = !app.isPackaged ? process.env['ELECTRON_RENDERER_URL'] : undefined
const EXTERNAL_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

export function devRendererUrl(): string | undefined {
  return devUrl
}

export function secureWebPreferences(): WebPreferences {
  return {
    preload: join(__dirname, '../preload/index.js'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
  }
}

/** True for the app's own renderer pages (dev server origin or packaged file://). */
function isAppUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    if (parsed.protocol === 'file:') return true
    return devUrl !== undefined && parsed.origin === new URL(devUrl).origin
  } catch {
    return false
  }
}

function openExternally(url: string): void {
  try {
    if (EXTERNAL_PROTOCOLS.has(new URL(url).protocol)) void shell.openExternal(url)
  } catch {
    // Malformed URL: ignore.
  }
}

/** Links never navigate our frameless windows; they open in the user's default browser. */
export function routeLinksExternally(win: BrowserWindow): void {
  win.webContents.setWindowOpenHandler(({ url }) => {
    openExternally(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (isAppUrl(url)) return
    e.preventDefault()
    openExternally(url)
  })
}

export function loadPage(win: BrowserWindow, page: RendererPage): void {
  routeLinksExternally(win)
  if (devUrl) {
    void win.loadURL(`${devUrl}/${page}/index.html`)
  } else {
    void win.loadFile(join(__dirname, `../renderer/${page}/index.html`))
  }
}
