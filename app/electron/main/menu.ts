/** The orb's right-click menu and system tray menu (PLAN §9.1, P8). */
import { Menu, type MenuItemConstructorOptions } from 'electron'

export interface MenuState {
  proactivePaused: boolean
  memoryOff: boolean
}

export interface MenuActions {
  openPanel: () => void
  toggleProactive: () => void
  toggleMemory: () => void
  openBrowser: () => void
  openSettings: () => void
  quit: () => void
}

export function buildAppMenu(actions: MenuActions, state: MenuState): Menu {
  const template: MenuItemConstructorOptions[] = [
    { label: 'Open assistant workspace', accelerator: 'Ctrl+Alt+P', click: actions.openPanel },
    {
      label: state.proactivePaused ? 'Resume proactive' : 'Pause proactive',
      click: actions.toggleProactive,
    },
    {
      label: state.memoryOff ? 'Memory on' : 'Memory off',
      click: actions.toggleMemory,
    },
    { label: 'Open Clickachu browser', click: actions.openBrowser },
    { label: 'Settings', click: actions.openSettings },
    { type: 'separator' },
    { label: 'Quit', click: actions.quit },
  ]
  return Menu.buildFromTemplate(template)
}
