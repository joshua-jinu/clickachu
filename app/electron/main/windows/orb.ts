/**
 * Orb window (PLAN §9.1): 120×120 transparent, click-through except over the sphere (the renderer
 * toggles that), dragged by main following the cursor, snapped to the nearest work-area edge on
 * release and persisted in electron-store.
 */
import { BrowserWindow, type Rectangle, screen } from 'electron'
import type { OrbDragPhase } from '../../../src/shared/bridge'
import { type Point, prefs } from '../store'
import { animateWindowPosition, loadPage, secureWebPreferences } from './common'

const SIZE = 120
const HIT_RADIUS = 48
const EDGE_MARGIN = 12
const HIT_POLL_MS = 40

let win: BrowserWindow | null = null
/** Cursor offset from the window's top-left while dragging. */
let dragOffset: Point | null = null
let expandedOnRight = false
let ignoringMouse = true
let hitPollTimer: ReturnType<typeof setInterval> | null = null
let workspaceBounds: Rectangle | null = null
let boundsBeforeWorkspace: Rectangle | null = null
let workspaceOffset: Point = { x: 0, y: 0 }

export function orbWindow(): BrowserWindow | null {
  return win
}

function isCursorOverInteractive(): boolean {
  if (!win || !win.isVisible()) return false
  if (dragOffset) return true
  const cursor = screen.getCursorScreenPoint()
  const b = win.getBounds()
  const lx = cursor.x - b.x
  const ly = cursor.y - b.y
  if (lx < 0 || ly < 0 || lx > b.width || ly > b.height) return false
  const orbCenterX = b.width - SIZE / 2
  const orbCenterY = SIZE / 2
  const overOrb = Math.hypot(lx - orbCenterX, ly - orbCenterY) <= HIT_RADIUS
  const overBubble =
    b.width > SIZE && lx >= 8 && lx <= b.width - SIZE - 12 && ly >= 12 && ly <= 108
  return overOrb || overBubble
}

function applyIgnoreMouse(ignore: boolean): void {
  if (!win || ignoringMouse === ignore) return
  ignoringMouse = ignore
  if (ignore) {
    win.setIgnoreMouseEvents(true, { forward: true })
  } else {
    win.setIgnoreMouseEvents(false)
  }
}

export function createOrbWindow(): BrowserWindow {
  expandedOnRight = false
  const pos = validPosition(prefs.get('orbPosition'))
  win = new BrowserWindow({
    x: pos.x,
    y: pos.y,
    width: SIZE,
    height: SIZE,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: false,
    resizable: false,
    movable: true,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    type: 'toolbar',
    title: 'Clickachu orb',
    webPreferences: secureWebPreferences(),
  })
  win.setAlwaysOnTop(true, 'screen-saver')
  ignoringMouse = true
  win.setIgnoreMouseEvents(true, { forward: true })
  // showInactive: never take focus from the user's app.
  win.once('ready-to-show', () => win?.showInactive())

  if (hitPollTimer) clearInterval(hitPollTimer)
  hitPollTimer = setInterval(() => {
    if (!win) return
    applyIgnoreMouse(!isCursorOverInteractive())
  }, HIT_POLL_MS)

  win.on('closed', () => {
    expandedOnRight = false
    if (hitPollTimer) {
      clearInterval(hitPollTimer)
      hitPollTimer = null
    }
    win = null
  })

  // A monitor unplugged or rearranged can strand the orb off-screen.
  const revalidate = (): void => {
    if (!win) return
    const b = win.getBounds()
    const next = validPosition({ x: b.x, y: b.y })
    if (next.x !== b.x || next.y !== b.y) moveTo(next, true)
  }
  screen.on('display-removed', revalidate)
  screen.on('display-metrics-changed', revalidate)
  win.on('closed', () => {
    screen.off('display-removed', revalidate)
    screen.off('display-metrics-changed', revalidate)
  })

  loadPage(win, 'orb')
  return win
}

export function setOrbClickThrough(enabled: boolean): void {
  // While dragging the renderer must keep receiving events.
  if (dragOffset && enabled) return
  if (enabled && isCursorOverInteractive()) return
  applyIgnoreMouse(enabled)
}

const EXPANDED_WIDTH = 340

/** Pixel centre of the particle sphere, including when its hover bubble expands the window. */
export function orbCenterPoint(): Point | null {
  if (!win || win.isDestroyed()) return null
  const bounds = win.getBounds()
  return {
    x: bounds.x + (bounds.width > SIZE && expandedOnRight ? bounds.width - SIZE / 2 : SIZE / 2),
    y: bounds.y + bounds.height / 2,
  }
}

export function setOrbExpanded(expanded: boolean): void {
  if (!win) return
  const b = win.getBounds()
  const display = screen.getDisplayMatching(b)
  if (expanded) {
    if (b.width === SIZE) {
      expandedOnRight = b.x + SIZE / 2 > display.workArea.x + display.workArea.width / 2
      const newX = expandedOnRight ? b.x - (EXPANDED_WIDTH - SIZE) : b.x
      win.setBounds({ x: newX, y: b.y, width: EXPANDED_WIDTH, height: SIZE })
    }
  } else {
    if (b.width === EXPANDED_WIDTH) {
      const newX = expandedOnRight ? b.x + (EXPANDED_WIDTH - SIZE) : b.x
      win.setBounds({ x: newX, y: b.y, width: SIZE, height: SIZE })
      expandedOnRight = false
    }
  }
}

/** Dock the orb beside the workspace and return its displacement from its saved home bounds. */
export function positionOrbForWorkspace(bounds: Rectangle): Point {
  if (!win) return { x: 0, y: 0 }
  if (boundsBeforeWorkspace === null) boundsBeforeWorkspace = win.getBounds()
  workspaceBounds = bounds
  const area = screen.getDisplayMatching(bounds).workArea
  const available = availableArea(area)
  const origin = boundsBeforeWorkspace
  const current = win.getBounds()
  const targetArea = available.width >= SIZE + EDGE_MARGIN * 2 ? available : area
  const minX = targetArea.x + EDGE_MARGIN
  const maxX = Math.max(minX, targetArea.x + targetArea.width - current.width - EDGE_MARGIN)
  const minY = targetArea.y + EDGE_MARGIN
  const maxY = Math.max(minY, targetArea.y + targetArea.height - current.height - EDGE_MARGIN)
  const centeredX = origin.x + (origin.width - current.width) / 2
  const centeredY = origin.y + (origin.height - current.height) / 2
  const x = Math.max(minX, Math.min(centeredX, maxX))
  const y = Math.max(minY, Math.min(centeredY, maxY))
  animateWindowPosition(win, x, y)
  workspaceOffset = {
    x: x + current.width / 2 - (origin.x + origin.width / 2),
    y: y + current.height / 2 - (origin.y + origin.height / 2),
  }
  return workspaceOffset
}

/** Current displacement from the orb's home position while Workspace is visible. */
export function orbWorkspaceOffset(): Point {
  return workspaceOffset
}

/** Restore the user's last compact orb position when the docked workspace is hidden. */
export function restoreOrbAfterWorkspace(): void {
  if (!win || boundsBeforeWorkspace === null) {
    workspaceBounds = null
    workspaceOffset = { x: 0, y: 0 }
    return
  }
  workspaceBounds = null
  const previous = boundsBeforeWorkspace
  boundsBeforeWorkspace = null
  workspaceOffset = { x: 0, y: 0 }
  const current = win.getBounds()
  const area = screen.getDisplayMatching(previous).workArea
  const minX = area.x + EDGE_MARGIN
  const maxX = Math.max(minX, area.x + area.width - current.width - EDGE_MARGIN)
  const minY = area.y + EDGE_MARGIN
  const maxY = Math.max(minY, area.y + area.height - current.height - EDGE_MARGIN)
  animateWindowPosition(
    win,
    Math.max(minX, Math.min(previous.x, maxX)),
    Math.max(minY, Math.min(previous.y, maxY)),
  )
}

export function orbDrag(phase: OrbDragPhase): void {
  if (!win) return
  const cursor = screen.getCursorScreenPoint()
  if (phase === 'start') {
    const b = win.getBounds()
    dragOffset = { x: cursor.x - b.x, y: cursor.y - b.y }
    applyIgnoreMouse(false)
  } else if (phase === 'move' && dragOffset) {
    const next = { x: cursor.x - dragOffset.x, y: cursor.y - dragOffset.y }
    moveTo(next, false)
  } else if (phase === 'end' && dragOffset) {
    dragOffset = null
    const b = win.getBounds()
    const area = availableArea(screen.getDisplayMatching(b).workArea)
    const snapped = snapToEdge(b, area)
    moveTo(snapped, false)
    prefs.set('orbPosition', { x: snapped.x, y: snapped.y })
    if (workspaceBounds) boundsBeforeWorkspace = win.getBounds()
  }
}

function moveTo(p: Point, persist: boolean): void {
  if (!win) return
  const b = win.getBounds()
  win.setBounds({
    x: Math.round(p.x),
    y: Math.round(p.y),
    width: b.width,
    height: b.height,
  })
  if (persist) prefs.set('orbPosition', { x: Math.round(p.x), y: Math.round(p.y) })
}

function availableArea(area: Rectangle): Rectangle {
  if (!workspaceBounds) return area
  const overlapsVertically =
    workspaceBounds.y < area.y + area.height && workspaceBounds.y + workspaceBounds.height > area.y
  const dockedRight = workspaceBounds.x + workspaceBounds.width >= area.x + area.width - 2
  if (!overlapsVertically || !dockedRight) return area
  return { ...area, width: Math.max(0, workspaceBounds.x - area.x) }
}

function clampToArea(p: Point, area: Rectangle): Point {
  return {
    x: Math.min(Math.max(p.x, area.x + EDGE_MARGIN), area.x + area.width - SIZE - EDGE_MARGIN),
    y: Math.min(Math.max(p.y, area.y + EDGE_MARGIN), area.y + area.height - SIZE - EDGE_MARGIN),
  }
}

/** Move to whichever work-area edge is closest, EDGE_MARGIN px in from it. */
function snapToEdge(b: Rectangle, area: Rectangle): Point {
  const width = Math.min(b.width, Math.max(SIZE, area.width - EDGE_MARGIN * 2))
  const height = Math.min(b.height, Math.max(SIZE, area.height - EDGE_MARGIN * 2))
  const p = {
    x: Math.min(Math.max(b.x, area.x + EDGE_MARGIN), area.x + area.width - width - EDGE_MARGIN),
    y: Math.min(Math.max(b.y, area.y + EDGE_MARGIN), area.y + area.height - height - EDGE_MARGIN),
  }
  const dist = {
    left: p.x - area.x,
    right: area.x + area.width - (p.x + width),
    top: p.y - area.y,
    bottom: area.y + area.height - (p.y + height),
  }
  const nearest = (Object.keys(dist) as (keyof typeof dist)[]).reduce((a, k) =>
    dist[k] < dist[a] ? k : a,
  )
  if (nearest === 'left') p.x = area.x + EDGE_MARGIN
  if (nearest === 'right') p.x = area.x + area.width - width - EDGE_MARGIN
  if (nearest === 'top') p.y = area.y + EDGE_MARGIN
  if (nearest === 'bottom') p.y = area.y + area.height - height - EDGE_MARGIN
  return p
}

function contains(area: Rectangle, p: Point): boolean {
  return p.x >= area.x && p.x < area.x + area.width && p.y >= area.y && p.y < area.y + area.height
}

/** The saved position if its centre is on a connected display, else the primary's right edge. */
function validPosition(saved: Point | undefined): Point {
  if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
    const centre = { x: saved.x + SIZE / 2, y: saved.y + SIZE / 2 }
    const display = screen.getAllDisplays().find((d) => contains(d.workArea, centre))
    if (display) return clampToArea(saved, display.workArea)
  }
  const area = screen.getPrimaryDisplay().workArea
  return {
    x: area.x + area.width - SIZE - EDGE_MARGIN,
    y: Math.round(area.y + area.height * 0.6 - SIZE / 2),
  }
}
