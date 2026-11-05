/**
 * Quick bar / 3D Orb HUD window (PLAN §9.2): 500 px frameless transparent card that opens in the
 * center-lower stage of the active display, sizes itself to the renderer's reported content height,
 * and hides on blur unless a run or voice session is active.
 */
import { BrowserWindow, type Rectangle, screen } from 'electron'
import { type QuickBarShown, IPC_QUICKBAR_SHOWN } from '../../../src/shared/bridge'
import type { ContextSnapshot, RunAction } from '../../../src/shared/protocol'
import { loadPage, secureWebPreferences } from './common'
import { panelWindow } from './panel'

const WIDTH = 500
const MIN_HEIGHT = 240
const MAX_HEIGHT_FRACTION = 0.68
const PANEL_WIDTH_RESERVE = 480

let win: BrowserWindow | null = null
let contentHeight = MIN_HEIGHT
let runActive = false
/** Work area of the display the bar was last opened on. */
let workArea: Rectangle | null = null

export function quickBarWindow(): BrowserWindow | null {
  return win
}

export function createQuickBarWindow(): BrowserWindow {
  win = new BrowserWindow({
    width: WIDTH,
    height: MIN_HEIGHT,
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
    // WS_EX_TOOLWINDOW: keeps it out of Alt+Tab as well as the taskbar.
    type: 'toolbar',
    title: 'Clickachu',
    webPreferences: secureWebPreferences(),
  })
  win.setAlwaysOnTop(true, 'pop-up-menu')

  win.on('blur', () => {
    if (!runActive) hideQuickBar()
  })
  win.on('closed', () => {
    win = null
  })

  loadPage(win, 'quickbar')
  return win
}

function maxHeight(area: Rectangle): number {
  return Math.max(MIN_HEIGHT, Math.floor(area.height * MAX_HEIGHT_FRACTION))
}

function clampHeight(h: number, area: Rectangle): number {
  return Math.min(maxHeight(area), Math.max(MIN_HEIGHT, Math.ceil(h)))
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), Math.max(lo, hi))
}

/** Center-stage HUD placement; shifts left if the Side Panel is visible so they never overlap. */
function boundsCenteredHud(area: Rectangle, height: number): Rectangle {
  const panelOpen = Boolean(panelWindow()?.isVisible())
  const reserve = panelWindow()?.getBounds().width ?? PANEL_WIDTH_RESERVE
  const availableWidth = panelOpen ? Math.max(WIDTH, area.width - reserve) : area.width
  const centeredX = area.x + Math.round((availableWidth - WIDTH) / 2)
  const x = clamp(centeredX, area.x + 16, area.x + area.width - WIDTH - 16)
  const targetY = area.y + Math.round(area.height * 0.52 - height / 2)
  const y = clamp(targetY, area.y + 24, area.y + area.height - height - 24)
  return { x: Math.round(x), y: Math.round(y), width: WIDTH, height }
}

export function nudgeQuickBarForPanel(): void {
  if (!win || !win.isVisible() || !workArea) return
  const b = win.getBounds()
  const reserve = panelWindow()?.getBounds().width ?? PANEL_WIDTH_RESERVE
  const maxRight = workArea.x + workArea.width - reserve
  if (b.x + b.width > maxRight) {
    const nextX = clamp(
      workArea.x + Math.round((workArea.width - reserve - WIDTH) / 2),
      workArea.x + 16,
      maxRight - WIDTH,
    )
    win.setBounds({ x: Math.round(nextX), y: b.y, width: WIDTH, height: b.height })
  }
}

export function openQuickBar(
  context: ContextSnapshot | null = null,
  initialAction?: RunAction,
  isListening?: boolean,
): void {
  if (!win) return
  const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint())
  workArea = display.workArea
  const height = clampHeight(contentHeight, workArea)
  if (!win.isVisible()) {
    win.setBounds(boundsCenteredHud(workArea, height))
    win.show()
  } else {
    nudgeQuickBarForPanel()
  }
  win.focus()
  win.webContents.send(IPC_QUICKBAR_SHOWN, {
    maxHeight: maxHeight(workArea),
    context,
    initialAction,
    isListening,
  } satisfies QuickBarShown)
}

export function hideQuickBar(): void {
  if (win?.isVisible()) win.hide()
}

export function setQuickBarRunActive(active: boolean): void {
  runActive = active
}

/** Grow/shrink to the renderer's content, keeping the top edge unless that would overflow. */
export function setQuickBarContentHeight(h: number): void {
  if (!Number.isFinite(h)) return
  contentHeight = h
  if (!win || !workArea) return
  const height = clampHeight(h, workArea)
  const b = win.getBounds()
  if (b.height === height) return
  const y = clamp(b.y, workArea.y + 20, workArea.y + workArea.height - height - 20)
  win.setBounds({ x: b.x, y, width: WIDTH, height })
}
