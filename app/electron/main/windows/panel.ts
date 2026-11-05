/**
 * Full-height, right-docked assistant sidebar. Close hides it so orb and Quick Bar handoffs can
 * reopen the same renderer and in-memory conversation state.
 */
import { app, BrowserWindow, dialog, screen, type Rectangle } from 'electron'
import {
  IPC_OPEN_SETTINGS,
  IPC_PANEL_HANDOFF,
  IPC_PANEL_SHOWN,
  type PanelHandoff,
} from '../../../src/shared/bridge'
import { prefs } from '../store'
import { loadPage, secureWebPreferences } from './common'
import { positionOrbForWorkspace, restoreOrbAfterWorkspace } from './orb'

const MIN_HEIGHT = 420

function dockWidth(workAreaWidth: number): number {
  return Math.max(1, Math.min(workAreaWidth, 440, Math.max(180, workAreaWidth - 144)))
}

function sidebarBounds(): Rectangle {
  const { workArea } = screen.getPrimaryDisplay()
  const width = dockWidth(workArea.width)
  return {
    x: workArea.x + workArea.width - width,
    y: workArea.y,
    width,
    height: workArea.height,
  }
}

let win: BrowserWindow | null = null
let quitting = false
app.on('before-quit', () => {
  quitting = true
})

export function panelWindow(): BrowserWindow | null {
  return win
}

export function createPanelWindow(): BrowserWindow {
  const bounds = sidebarBounds()
  win = new BrowserWindow({
    ...bounds,
    show: false,
    frame: false,
    backgroundColor: '#080a0d',
    hasShadow: false,
    minWidth: Math.min(180, bounds.width),
    minHeight: Math.min(MIN_HEIGHT, bounds.height),
    resizable: false,
    movable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    autoHideMenuBar: true,
    title: 'Clickachu — Assistant',
    webPreferences: secureWebPreferences(),
  })
  const saveBounds = (): void => {
    if (!win || win.isMaximized() || win.isMinimized()) return
    const b = win.getBounds()
    prefs.set('assistantBounds', { x: b.x, y: b.y, width: b.width, height: b.height })
    if (win.isVisible()) positionOrbForWorkspace(b)
  }
  win.on('resize', saveBounds)
  win.on('move', saveBounds)
  win.on('close', (e) => {
    if (quitting) return
    e.preventDefault()
    hidePanel()
  })
  win.on('closed', () => {
    win = null
  })
  loadPage(win, 'panel')
  return win
}

export function showPanel(takeFocus = true): void {
  if (!win) createPanelWindow()
  if (!win) return
  const bounds = sidebarBounds()
  // Reassert native geometry on every open; hidden windows can retain stale bounds across
  // display/work-area changes. Restore first so minimized/maximized state cannot override it.
  if (win.isMinimized()) win.restore()
  if (win.isMaximized()) win.unmaximize()
  win.setBounds(bounds)
  positionOrbForWorkspace(bounds)
  if (takeFocus) {
    win.show()
    win.focus()
  } else {
    win.showInactive()
  }
  win.webContents.send(IPC_PANEL_SHOWN)
}

export function openPanelWithHandoff(handoff?: PanelHandoff): void {
  showPanel(!handoff?.keepHudOpen)
  if (!win || !handoff) return
  if (win.webContents.isLoading()) {
    win.webContents.once('did-finish-load', () => {
      win?.webContents.send(IPC_PANEL_HANDOFF, handoff)
    })
  } else {
    win.webContents.send(IPC_PANEL_HANDOFF, handoff)
  }
}

export function openPanelInSettings(): void {
  showPanel()
  if (!win) return
  if (win.webContents.isLoading()) {
    win.webContents.once('did-finish-load', () => {
      win?.webContents.send(IPC_OPEN_SETTINGS)
    })
  } else {
    win.webContents.send(IPC_OPEN_SETTINGS)
  }
}

export function hidePanel(): void {
  if (win?.isVisible()) {
    win.hide()
    restoreOrbAfterWorkspace()
  }
}

export function togglePanel(): void {
  if (win?.isVisible()) hidePanel()
  else showPanel()
}

/** Native folder picker parented to the panel; null when cancelled. */
export async function pickFolder(): Promise<string | null> {
  if (!win) return null
  const res = await dialog.showOpenDialog(win, { properties: ['openDirectory'] })
  return res.canceled ? null : (res.filePaths[0] ?? null)
}
