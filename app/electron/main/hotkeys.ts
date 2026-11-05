/** Global hotkeys (PLAN §9.5, §17). */
import { globalShortcut } from 'electron'

export interface HotkeyActions {
  openQuickBar: () => void
  togglePanel: () => void
}

export function registerHotkeys(actions: HotkeyActions): void {
  const bindings: [string, () => void][] = [
    ['Alt+Q', actions.openQuickBar],
    ['Control+Alt+P', actions.togglePanel],
  ]
  for (const [accel, fn] of bindings) {
    // register() returns false (no throw) when another app already owns the combination.
    const ok = globalShortcut.register(accel, fn)
    if (!ok) console.error(`[hotkeys] could not register ${accel} (in use by another app?)`)
  }
}

export function unregisterHotkeys(): void {
  globalShortcut.unregisterAll()
}
