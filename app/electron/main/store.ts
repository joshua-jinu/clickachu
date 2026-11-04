/** Persistent shell preferences (electron-store, PLAN §5). Not for secrets. */
import { join } from 'node:path'
import { app } from 'electron'
import Store from 'electron-store'

export interface Point {
  x: number
  y: number
}

interface ShellPrefs {
  orbPosition?: Point
}

// %APPDATA%\Clickachu\shell.json, next to the sidecar's data (CLAUDE.md "Runtime data").
export const prefs = new Store<ShellPrefs>({
  name: 'shell',
  cwd: join(app.getPath('appData'), 'Clickachu'),
})
