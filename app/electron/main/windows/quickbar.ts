/**
 * Quick bar window (PLAN §9.2): the single voice + text HUD for both Alt+Q and orb clicks.
 * The original desktop orb stays visible; this window contains only its adjacent composer. It
 * sizes itself to the renderer's reported content height and hides on blur unless a run or voice
 * session is active.
 */
import { BrowserWindow, type Rectangle, screen } from 'electron'
import {
  IPC_QUICKBAR_SHOWN,
  IPC_QUICKBAR_HIDDEN,
  type QuickBarAnchor,
  type QuickBarShown,
} from '../../../src/shared/bridge'
import type { ContextSnapshot, RunAction } from '../../../src/shared/protocol'
import type { Point } from '../store'
import { animateWindowPosition, loadPage, secureWebPreferences } from './common'
import { orbCenterPoint, orbWindow, orbWorkspaceOffset } from './orb'
import { panelWindow, registerQuickBarWorkspacePositioning } from './panel'

const WIDTH = 360
const MIN_HEIGHT = 250
const MAX_HEIGHT_FRACTION = 0.68
const PANEL_WIDTH_RESERVE = 480
const EDGE = 12
const ORB_GAP = 12
const ORB_SIZE = 120

let win: BrowserWindow | null = null
let rendererReady = false
let pendingShownInfo: QuickBarShown | null = null
let contentHeight = MIN_HEIGHT
let runActive = false
/** Work area of the display the bar was last opened on. */
let workArea: Rectangle | null = null
/** Centre of the desktop orb when the bar was opened from it; null for center-stage opens. */
let orbCenter: Point | null = null
let side: 'left' | 'right' = 'left'
let quickBarWorkspaceOrigin: Rectangle | null = null
let quickBarWorkspaceOrbOffset: Point = { x: 0, y: 0 }
let quickBarWorkspaceOrbCenter: Point | null = null

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
  rendererReady = false
  pendingShownInfo = null
  win.webContents.on('did-start-loading', () => {
    rendererReady = false
    pendingShownInfo = null
  })
  win.setAlwaysOnTop(true, 'pop-up-menu')

  win.on('blur', () => {
    if (!runActive && !panelWindow()?.isVisible()) hideQuickBar()
  })
  win.on('closed', () => {
    win = null
    rendererReady = false
    pendingShownInfo = null
    quickBarWorkspaceOrigin = null
    quickBarWorkspaceOrbOffset = { x: 0, y: 0 }
    quickBarWorkspaceOrbCenter = null
  })

  loadPage(win, 'quickbar')
  return win
}

/** Called after the QuickBar renderer subscribes to visibility events. */
export function setQuickBarRendererReady(): void {
  rendererReady = true
  if (win?.isVisible() && pendingShownInfo) {
    win.webContents.send(IPC_QUICKBAR_SHOWN, pendingShownInfo)
    pendingShownInfo = null
  }
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

/** Keep the composer beside the desktop orb, leaving the orb at its original screen position. */
function boundsAtOrb(area: Rectangle, height: number, center: Point): Rectangle {
  const orbLeft = center.x - ORB_SIZE / 2
  const orbRight = center.x + ORB_SIZE / 2
  const minX = area.x + EDGE
  const maxX = usableRight(area) - WIDTH - EDGE
  const preferredX = side === 'right' ? orbLeft - ORB_GAP - WIDTH : orbRight + ORB_GAP
  const alternateX = side === 'right' ? orbRight + ORB_GAP : orbLeft - ORB_GAP - WIDTH
  const fits = (x: number) => x >= minX && x <= maxX
  const x = fits(preferredX) ? preferredX : fits(alternateX) ? alternateX : preferredX
  const y = center.y - Math.round(height / 2)
  return {
    x: Math.round(clamp(x, minX, maxX)),
    y: Math.round(clamp(y, area.y + EDGE, area.y + area.height - height - EDGE)),
    width: WIDTH,
    height,
  }
}

function placement(area: Rectangle, height: number): Rectangle {
  return orbCenter ? boundsAtOrb(area, height, orbCenter) : boundsCentered(area, height)
}

export function nudgeQuickBarForPanel(): void {
  if (!win || !win.isVisible() || !workArea) return
  const panel = panelWindow()
  if (panel?.isVisible() && orbCenter) {
    positionQuickBarForWorkspace(panel.getBounds(), orbWorkspaceOffset())
    return
  }
  const b = win.getBounds()
  const maxRight = usableRight(workArea) - EDGE
  if (b.x + b.width > maxRight) {
    const nextX = clamp(maxRight - WIDTH, workArea.x + EDGE, maxRight - WIDTH)
    win.setBounds({ x: Math.round(nextX), y: b.y, width: WIDTH, height: b.height })
  }
}

function captureQuickBarWorkspaceOrigin(): void {
  if (!win || !win.isVisible() || quickBarWorkspaceOrigin) return
  quickBarWorkspaceOrigin = win.getBounds()
  quickBarWorkspaceOrbOffset = { x: 0, y: 0 }
  quickBarWorkspaceOrbCenter = orbCenter ? { ...orbCenter } : null
}

function positionQuickBarForWorkspace(panelBounds: Rectangle, orbOffset: Point): void {
  if (!win || !win.isVisible() || !workArea || !orbCenter) return
  captureQuickBarWorkspaceOrigin()
  const origin = quickBarWorkspaceOrigin
  const originCenter = quickBarWorkspaceOrbCenter
  if (!origin || !originCenter) return

  const area = screen.getDisplayMatching(panelBounds).workArea
  const relativeShift = {
    x: orbOffset.x - quickBarWorkspaceOrbOffset.x,
    y: orbOffset.y - quickBarWorkspaceOrbOffset.y,
  }
  const minX = area.x + EDGE
  const maxX = Math.min(area.x + area.width - WIDTH - EDGE, panelBounds.x - WIDTH - EDGE)
  if (maxX < minX) return
  const current = win.getBounds()
  const targetX = clamp(origin.x + relativeShift.x, minX, maxX)
  const targetY = clamp(
    origin.y + relativeShift.y,
    area.y + EDGE,
    area.y + area.height - current.height - EDGE,
  )
  animateWindowPosition(win, targetX, targetY)
  orbCenter = {
    x: originCenter.x + targetX - origin.x,
    y: originCenter.y + targetY - origin.y,
  }
}

function captureQuickBarOpenedDuringWorkspace(): void {
  if (!panelWindow()?.isVisible() || !win?.isVisible() || !orbCenter) return
  quickBarWorkspaceOrigin = win.getBounds()
  quickBarWorkspaceOrbOffset = orbWorkspaceOffset()
  quickBarWorkspaceOrbCenter = orbCenter ? { ...orbCenter } : null
}

function restoreQuickBarAfterWorkspace(): void {
  const origin = quickBarWorkspaceOrigin
  const originCenter = quickBarWorkspaceOrbCenter
  const baselineOffset = quickBarWorkspaceOrbOffset
  quickBarWorkspaceOrigin = null
  quickBarWorkspaceOrbOffset = { x: 0, y: 0 }
  quickBarWorkspaceOrbCenter = null
  if (!origin || !win || win.isDestroyed()) return

  const current = win.getBounds()
  const area = workArea ?? screen.getPrimaryDisplay().workArea
  const targetX = clamp(origin.x - baselineOffset.x, area.x + EDGE, area.x + area.width - WIDTH - EDGE)
  const targetY = clamp(origin.y - baselineOffset.y, area.y + EDGE, area.y + area.height - current.height - EDGE)
  animateWindowPosition(win, targetX, targetY)
  if (originCenter) {
    orbCenter = {
      x: originCenter.x + targetX - origin.x,
      y: originCenter.y + targetY - origin.y,
    }
  }
}

export function openQuickBar(
  context: ContextSnapshot | null = null,
  initialAction?: RunAction,
  _isListening?: boolean,
  anchor: QuickBarAnchor = 'center',
): void {
  if (!win) return
  const focusInput = !win.isVisible() || win.isMinimized()
  if (win.isMinimized()) win.restore()
  if (!win.isVisible()) {
    const orb = anchor === 'orb' ? orbWindow() : null
    if (orb?.isVisible()) {
      const o = orb.getBounds()
      const display = screen.getDisplayMatching(o)
      workArea = display.workArea
      orbCenter = orbCenterPoint() ?? { x: o.x + ORB_SIZE / 2, y: o.y + o.height / 2 }
      const onRight = orbCenter.x >= workArea.x + workArea.width / 2
      side = onRight ? 'right' : 'left'
    } else {
      workArea = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
      orbCenter = null
      side = 'left'
    }
    win.setBounds(placement(workArea, clampHeight(contentHeight, workArea)))
    win.show()
    captureQuickBarOpenedDuringWorkspace()
  } else {
    nudgeQuickBarForPanel()
  }
  win.focus()
  win.webContents.focus()
  const shownInfo: QuickBarShown = {
    maxHeight: maxHeight(workArea ?? screen.getPrimaryDisplay().workArea),
    context,
    initialAction,
    isListening: false,
    focusInput,
    side,
  }
  if (rendererReady) {
    win.webContents.send(IPC_QUICKBAR_SHOWN, shownInfo)
  } else {
    pendingShownInfo = shownInfo
  }
}

export function hideQuickBar(): void {
  pendingShownInfo = null
  if (win?.isVisible()) {
    win.webContents.send(IPC_QUICKBAR_HIDDEN)
    win.hide()
  }
  if (panelWindow()?.isVisible()) {
    quickBarWorkspaceOrigin = null
    quickBarWorkspaceOrbOffset = { x: 0, y: 0 }
    quickBarWorkspaceOrbCenter = null
  }
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

registerQuickBarWorkspacePositioning({
  hide: hideQuickBar,
  capture: captureQuickBarWorkspaceOrigin,
  position: positionQuickBarForWorkspace,
  restore: restoreQuickBarAfterWorkspace,
})
