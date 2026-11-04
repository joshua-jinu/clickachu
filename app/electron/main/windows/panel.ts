/**
 * Side panel window (PLAN §9.3): 380 px wide, work-area tall, docked to the right edge of the
 * display the cursor is on, frameless. The slide-in itself is a CSS transition in the renderer,
 * re-triggered by IPC_PANEL_SHOWN each time the window is shown.
 */
import { app, BrowserWindow, dialog, screen } from 'electron'
import {
  IPC_OPEN_SETTINGS,
  IPC_PANEL_HANDOFF,
  IPC_PANEL_SHOWN,
  type PanelHandoff,
} from '../../../src/shared/bridge'
import { loadPage, secureWebPreferences } from './common'
import { nudgeQuickBarForPanel } from './quickbar'

const WIDTH = 400

let win: BrowserWindow | null = null
let quitting = false
app.on('before-quit', () => {
  quitting = true
})

export function panelWindow(): BrowserWindow | null {
  return win
}

export function createPanelWindow(): BrowserWindow {
  win = new BrowserWindow({
    width: WIDTH,
    height: 720,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: false,
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    title: 'Clickachu',
    webPreferences: secureWebPreferences(),
  })
  win.setAlwaysOnTop(true, 'floating')
  win.on('close', (e) => {
    if (quitting) return
    e.preventDefault()
    win?.hide()
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
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
  win.setBounds({ x: area.x + area.width - WIDTH, y: area.y, width: WIDTH, height: area.height })
  if (takeFocus) {
    win.show()
    win.focus()
  } else {
    win.showInactive()
  }
  nudgeQuickBarForPanel()
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
  if (win?.isVisible()) win.hide()
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
