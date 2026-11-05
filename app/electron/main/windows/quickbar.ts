/**
 * Quick bar window (PLAN §9.2): the single voice + text HUD for both Alt+Q and orb clicks.
 * The layout is the orb grown beside a compact composer card. Opened from the orb, it takes the
 * orb's place on screen (the desktop orb hides meanwhile, so there is only ever one orb); opened
 * with Alt+Q it sits center stage. It sizes itself to the renderer's reported content height and
 * hides on blur unless a run or voice session is active.
 */
import { BrowserWindow, type Rectangle, screen } from 'electron'
import {
  IPC_QUICKBAR_SHOWN,
  type QuickBarAnchor,
  type QuickBarShown,
} from '../../../src/shared/bridge'
import type { ContextSnapshot, RunAction } from '../../../src/shared/protocol'
import type { Point } from '../store'
import { loadPage, secureWebPreferences } from './common'
import { orbWindow } from './orb'
import { panelWindow } from './panel'

const WIDTH = 540
const MIN_HEIGHT = 250
const MAX_HEIGHT_FRACTION = 0.68
const PANEL_WIDTH_RESERVE = 480
const EDGE = 12
/** Distance from the bar's edge to the centre of its orb column (4px padding + 194px / 2). */
const ORB_COLUMN_CENTER = 101

let win: BrowserWindow | null = null
let contentHeight = MIN_HEIGHT
let runActive = false
/** Work area of the display the bar was last opened on. */
let workArea: Rectangle | null = null
/** Centre of the desktop orb when the bar was opened from it; null for center-stage opens. */
let orbCenter: Point | null = null
let side: 'left' | 'right' = 'left'
let orbHidden = false

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
  win.on('hide', restoreOrb)
  win.on('closed', () => {
    win = null
    restoreOrb()
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

/** Right edge usable by the bar: the work area, minus the side panel when it is docked open. */
function usableRight(area: Rectangle): number {
  const panel = panelWindow()
  if (!panel?.isVisible()) return area.x + area.width
  return Math.min(area.x + area.width, panel.getBounds().x)
}

/** Center-stage placement; shifts left if the side panel is visible so they never overlap. */
function boundsCentered(area: Rectangle, height: number): Rectangle {
  const panelOpen = Boolean(panelWindow()?.isVisible())
  const reserve = panelWindow()?.getBounds().width ?? PANEL_WIDTH_RESERVE
  const availableWidth = panelOpen ? Math.max(WIDTH, area.width - reserve) : area.width
  const centeredX = area.x + Math.round((availableWidth - WIDTH) / 2)
  const x = clamp(centeredX, area.x + EDGE, area.x + area.width - WIDTH - EDGE)
  const targetY = area.y + Math.round(area.height * 0.52 - height / 2)
  const y = clamp(targetY, area.y + 24, area.y + area.height - height - 24)
  return { x: Math.round(x), y: Math.round(y), width: WIDTH, height }
}

/** The bar's orb column lands where the desktop orb was; the composer extends toward the screen. */
function boundsAtOrb(area: Rectangle, height: number, center: Point): Rectangle {
  const x =
    side === 'right' ? center.x + ORB_COLUMN_CENTER - WIDTH : center.x - ORB_COLUMN_CENTER
  const y = center.y - Math.round(height / 2)
  return {
    x: Math.round(clamp(x, area.x + EDGE, usableRight(area) - WIDTH - EDGE)),
    y: Math.round(clamp(y, area.y + EDGE, area.y + area.height - height - EDGE)),
    width: WIDTH,
    height,
  }
}

function placement(area: Rectangle, height: number): Rectangle {
  return orbCenter ? boundsAtOrb(area, height, orbCenter) : boundsCentered(area, height)
}

function restoreOrb(): void {
  if (!orbHidden) return
  orbHidden = false
  const orb = orbWindow()
  if (orb && !orb.isDestroyed()) orb.showInactive()
}

export function nudgeQuickBarForPanel(): void {
  if (!win || !win.isVisible() || !workArea) return
  const b = win.getBounds()
  const maxRight = usableRight(workArea) - EDGE
  if (b.x + b.width > maxRight) {
    const nextX = clamp(maxRight - WIDTH, workArea.x + EDGE, maxRight - WIDTH)
    win.setBounds({ x: Math.round(nextX), y: b.y, width: WIDTH, height: b.height })
  }
}

export function openQuickBar(
  context: ContextSnapshot | null = null,
  initialAction?: RunAction,
  isListening?: boolean,
  anchor: QuickBarAnchor = 'center',
): void {
  if (!win) return
  if (!win.isVisible()) {
    const orb = anchor === 'orb' ? orbWindow() : null
    if (orb?.isVisible()) {
      const o = orb.getBounds()
      const display = screen.getDisplayMatching(o)
      workArea = display.workArea
      orbCenter = { x: o.x + o.width / 2, y: o.y + o.height / 2 }
      side = orbCenter.x >= workArea.x + workArea.width / 2 ? 'right' : 'left'
    } else {
      workArea = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
      orbCenter = null
      side = 'left'
    }
    win.setBounds(placement(workArea, clampHeight(contentHeight, workArea)))
    win.show()
    // One orb on screen: the bar's own orb stands in for the desktop orb while open.
    if (orbCenter && orb?.isVisible()) {
      orb.hide()
      orbHidden = true
    }
  } else {
    nudgeQuickBarForPanel()
  }
  win.focus()
  win.webContents.send(IPC_QUICKBAR_SHOWN, {
    maxHeight: maxHeight(workArea ?? screen.getPrimaryDisplay().workArea),
    context,
    initialAction,
    isListening,
    side,
  } satisfies QuickBarShown)
}

export function hideQuickBar(): void {
  if (win?.isVisible()) win.hide()
  restoreOrb()
}

export function setQuickBarRunActive(active: boolean): void {
  runActive = active
}

/** Grow/shrink to the renderer's content, keeping the orb in place when anchored to it. */
export function setQuickBarContentHeight(h: number): void {
  if (!Number.isFinite(h)) return
  contentHeight = h
  if (!win || !workArea) return
  const height = clampHeight(h, workArea)
  const b = win.getBounds()
  if (b.height === height) return
  if (orbCenter) {
    win.setBounds({ ...boundsAtOrb(workArea, height, orbCenter), x: b.x })
    return
  }
  const y = clamp(b.y, workArea.y + 20, workArea.y + workArea.height - height - 20)
  win.setBounds({ x: b.x, y, width: WIDTH, height })
}
