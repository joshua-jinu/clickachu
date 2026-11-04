/**
 * System tray icon and menu (PLAN §9.1, §9.4, P8).
 */
import { app, type Menu, nativeImage, Notification, Tray } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { buildAppMenu, type MenuActions, type MenuState } from './menu'

let tray: Tray | null = null
let menuState: MenuState = { proactivePaused: false, memoryOff: false }
let currentActions: MenuActions | null = null

export function getOrbIconPath(): string {
  // Check typical locations in dev and packaged modes
  const candidates = [
    join(app.getAppPath(), 'resources', 'orb.png'),
    join(app.getAppPath(), 'out', 'resources', 'orb.png'),
    join(process.cwd(), 'resources', 'orb.png'),
    join(process.cwd(), 'app', 'resources', 'orb.png'),
  ]
  for (const c of candidates) {
    if (existsSync(c)) return c
  }
  return candidates[0]
}

export function createTray(actions: MenuActions): Tray {
  currentActions = actions
  const icon = nativeImage.createFromPath(getOrbIconPath())
  tray = new Tray(icon)
  tray.setToolTip('Clickachu')

  updateTrayMenu()

  tray.on('click', () => {
    actions.openPanel()
  })

  return tray
}

export function updateMenuState(patch: Partial<MenuState>): void {
  menuState = { ...menuState, ...patch }
  updateTrayMenu()
}

export function getAppMenu(): Menu | null {
  if (!currentActions) return null
  return buildAppMenu(currentActions, menuState)
}

function updateTrayMenu(): void {
  if (!tray || !currentActions) return
  const menu = buildAppMenu(currentActions, menuState)
  tray.setContextMenu(menu)
}

export function showWindowsNotification(title: string, body: string, onClick?: () => void): void {
  if (!Notification.isSupported()) return
  const icon = nativeImage.createFromPath(getOrbIconPath())
  const notif = new Notification({
    title,
    body,
    icon,
  })
  if (onClick) {
    notif.on('click', onClick)
  }
  notif.show()
}
