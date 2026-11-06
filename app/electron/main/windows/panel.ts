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
import { prefs, type Point } from '../store'
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
let rendererReady = false
let pendingPanelFocus: boolean | null = null
let quitting = false
let quickBarWorkspacePositioning: {
  hide: () => void
  capture: () => void
  position: (bounds: Rectangle, orbOffset: Point) => void
  restore: () => void
} | null = null
app.on('before-quit', () => {
  quitting = true
})

export function registerQuickBarWorkspacePositioning(
  handlers: NonNullable<typeof quickBarWorkspacePositioning>,
): void {
  quickBarWorkspacePositioning = handlers
}

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
  rendererReady = false
  pendingPanelFocus = null
  win.webContents.on('did-start-loading', () => {
    rendererReady = false
    pendingPanelFocus = null
  })
  const saveBounds = (): void => {
    if (!win || win.isMaximized() || win.isMinimized()) return
    const b = win.getBounds()
    prefs.set('assistantBounds', { x: b.x, y: b.y, width: b.width, height: b.height })
    if (win.isVisible()) {
      const orbOffset = positionOrbForWorkspace(b)
      quickBarWorkspacePositioning?.position(b, orbOffset)
    }
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
    rendererReady = false
    pendingPanelFocus = null
  })
  loadPage(win, 'panel')
  return win
}

/** Called after Panel subscribes to the shown event. */
export function setPanelRendererReady(): void {
  rendererReady = true
  if (win?.isVisible() && pendingPanelFocus !== null) {
    win.webContents.send(IPC_PANEL_SHOWN, pendingPanelFocus)
    pendingPanelFocus = null
  }
}

function notifyPanelShown(focusComposer: boolean): void {
  if (!win) return
  if (rendererReady) {
    win.webContents.send(IPC_PANEL_SHOWN, focusComposer)
  } else {
    pendingPanelFocus = focusComposer
  }
}

export function showPanel(takeFocus = true, focusComposer = takeFocus): void {
  if (!win) createPanelWindow()
  if (!win) return
  quickBarWorkspacePositioning?.hide()
  const focusOnOpen = focusComposer && (!win.isVisible() || win.isMinimized())
  const bounds = sidebarBounds()
  // Reassert native geometry on every open; hidden windows can retain stale bounds across
  // display/work-area changes. Restore first so minimized/maximized state cannot override it.
  if (win.isMinimized()) win.restore()
  if (win.isMaximized()) win.unmaximize()
  win.setBounds(bounds)
  quickBarWorkspacePositioning?.capture()
  const orbOffset = positionOrbForWorkspace(bounds)
  quickBarWorkspacePositioning?.position(bounds, orbOffset)
  if (takeFocus) {
    win.show()
    win.focus()
  } else {
    win.showInactive()
  }
  notifyPanelShown(focusOnOpen)
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
  showPanel(true, false)
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
    pendingPanelFocus = null
    win.hide()
    restoreOrbAfterWorkspace()
    quickBarWorkspacePositioning?.restore()
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
