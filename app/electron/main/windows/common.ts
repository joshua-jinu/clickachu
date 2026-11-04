/** Shared window plumbing: hardened webPreferences (§6.8) and renderer page loading. */
import { join } from 'node:path'
import { app, type BrowserWindow, type WebPreferences } from 'electron'

export type RendererPage = 'orb' | 'quickbar' | 'panel'

const devUrl = !app.isPackaged ? process.env['ELECTRON_RENDERER_URL'] : undefined

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

export function loadPage(win: BrowserWindow, page: RendererPage): void {
  if (devUrl) {
    void win.loadURL(`${devUrl}/${page}/index.html`)
  } else {
    void win.loadFile(join(__dirname, `../renderer/${page}/index.html`))
  }
}
