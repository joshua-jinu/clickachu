import { app, ipcMain, type IpcMainEvent, type WebContents } from 'electron'
import {
  IPC_BACKEND,
  IPC_CONTEXT_MENU,
  IPC_OPEN_PANEL_CHAT,
  IPC_OPEN_QUICKBAR,
  IPC_OPEN_SETTINGS,
  IPC_ORB_CLICK_THROUGH,
  IPC_ORB_DRAG,
  IPC_ORB_EXPANDED,
  IPC_ORB_INTERFACE_EXPANDED,
  IPC_PANEL_HIDE,
  IPC_PICK_FOLDER,
  IPC_QUICKBAR_HEIGHT,
  IPC_QUICKBAR_HIDE,
  IPC_QUICKBAR_RUN_ACTIVE,
  IPC_SHOW_NOTIFICATION,
  IPC_TOGGLE_PANEL,
  IPC_VOICE_TRANSCRIPT,
  type OrbDragPhase,
  type PanelHandoff,
} from '../../src/shared/bridge'
import type { ContextSnapshot, RunAction, Settings, SettingsView } from '../../src/shared/protocol'
import { captureContext, createOpenWithContext } from './capture'
import { registerHotkeys, unregisterHotkeys } from './hotkeys'
import type { MenuActions } from './menu'
import { defaultCoreDir, defaultLogDir, Sidecar } from './sidecar'
import { createTray, getAppMenu, showWindowsNotification, updateMenuState } from './tray'
import { devRendererUrl } from './windows/common'
import {
  createOrbWindow,
  orbDrag,
  orbWindow,
  setOrbClickThrough,
  setOrbExpanded,
  setOrbInterfaceExpanded,
} from './windows/orb'
import {
  createPanelWindow,
  hidePanel,
  openPanelInSettings,
  openPanelWithHandoff,
  panelWindow,
  pickFolder,
  showPanel,
  togglePanel,
} from './windows/panel'
import {
  createQuickBarWindow,
  hideQuickBar,
  openQuickBar,
  quickBarWindow,
  setQuickBarContentHeight,
  setQuickBarRunActive,
} from './windows/quickbar'

// A second instance couldn't register the hotkeys and would spawn a second sidecar.
if (!app.requestSingleInstanceLock()) app.quit()

// On Windows, multiple transparent DirectComposition windows combined with WebGL shaders
// can trigger GPU sandbox access violations (STATUS_ACCESS_VIOLATION / exit_code -1073741819).
if (process.platform === 'win32') {
  app.commandLine.appendSwitch('disable-gpu-sandbox')
}

const devUrl = devRendererUrl()
const sidecar = new Sidecar(
  defaultCoreDir(),
  defaultLogDir(),
  devUrl ? new URL(devUrl).origin : undefined,
)

let voiceRecording = false
let currentSettings: Settings | null = null

async function sidecarRequest<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  try {
    const { baseUrl, token } = await sidecar.backend()
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) return null
    return (await res.json()) as T
  } catch (err) {
    console.error(`[sidecar] ${method} ${path} failed:`, err)
    return null
  }
}

async function syncSettingsState(): Promise<void> {
  const settings = await sidecarRequest<SettingsView>('GET', '/settings')
  if (settings) {
    currentSettings = settings
    updateMenuState({
      proactivePaused: !settings.proactive_suggestions,
      memoryOff: !settings.memory_enabled,
    })
  }
}

const menuActions: MenuActions = {
  openPanel: () => showPanel(),
  toggleProactive: async () => {
    const next = !currentSettings?.proactive_suggestions
    const updated = await sidecarRequest<SettingsView>('PUT', '/settings', {
      proactive_suggestions: next,
    })
    if (updated) {
      currentSettings = updated
      updateMenuState({ proactivePaused: !updated.proactive_suggestions })
    }
  },
  toggleMemory: async () => {
    const next = !currentSettings?.memory_enabled
    const updated = await sidecarRequest<SettingsView>('PUT', '/settings', {
      memory_enabled: next,
    })
    if (updated) {
      currentSettings = updated
      updateMenuState({ memoryOff: !updated.memory_enabled })
    }
  },
  openBrowser: async () => {
    await sidecarRequest<{ ok: boolean }>('POST', '/browser/open')
  },
  openSettings: () => {
    openPanelInSettings()
  },
  quit: () => {
    app.quit()
  },
}

const toggleVoice = async () => {
  if (!voiceRecording) {
    voiceRecording = true
    const res = await sidecarRequest<{ ok: boolean }>('POST', '/voice/start')
    if (res?.ok) {
      openQuickBar(null, undefined, true)
    } else {
      voiceRecording = false
    }
  } else {
    voiceRecording = false
    const res = await sidecarRequest<{ text: string }>('POST', '/voice/stop')
    if (res?.text !== undefined) {
      quickBarWindow()?.webContents.send(IPC_VOICE_TRANSCRIPT, res.text)
    }
  }
}

const hotkeyActions = {
  openQuickBar: createOpenWithContext(sidecar),
  togglePanel,
  toggleVoice,
}

/** Only accept a window-specific IPC call from that window. */
function from(
  owner: () => { webContents: WebContents } | null,
  handler: (e: IpcMainEvent, ...args: unknown[]) => void,
): (e: IpcMainEvent, ...args: unknown[]) => void {
  return (e, ...args) => {
    if (owner()?.webContents === e.sender) handler(e, ...args)
  }
}

ipcMain.handle(IPC_BACKEND, () => sidecar.backend())
ipcMain.on(
  IPC_OPEN_QUICKBAR,
  (_e, payload?: { context?: ContextSnapshot | null; initialAction?: RunAction; isListening?: boolean }) => {
    void (async () => {
      let ctx = payload?.context ?? null
      if (!ctx && !quickBarWindow()?.isVisible()) {
        ctx = await captureContext(sidecar)
      }
      if (payload?.isListening && currentSettings?.voice_enabled !== false) {
        const res = await sidecarRequest<{ ok: boolean }>('POST', '/voice/start')
        voiceRecording = Boolean(res?.ok)
        openQuickBar(ctx, payload?.initialAction, voiceRecording || payload.isListening)
        return
      }
      openQuickBar(ctx, payload?.initialAction, payload?.isListening)
    })()
  },
)
ipcMain.on(IPC_TOGGLE_PANEL, () => togglePanel())
ipcMain.on(IPC_OPEN_SETTINGS, () => openPanelInSettings())
ipcMain.on(IPC_OPEN_PANEL_CHAT, (_e, handoff: unknown) => {
  const valid =
    handoff && typeof handoff === 'object' &&
    (('runId' in handoff && 'question' in handoff) || 'contextId' in handoff)
      ? (handoff as PanelHandoff)
      : undefined
  openPanelWithHandoff(valid)
})
ipcMain.on(
  IPC_PANEL_HIDE,
  from(panelWindow, () => hidePanel()),
)
ipcMain.handle(IPC_PICK_FOLDER, (e) =>
  panelWindow()?.webContents === e.sender ? pickFolder() : null,
)

ipcMain.on(
  IPC_QUICKBAR_HEIGHT,
  from(quickBarWindow, (_e, h) => {
    if (typeof h === 'number') setQuickBarContentHeight(h)
  }),
)
ipcMain.on(
  IPC_QUICKBAR_HIDE,
  from(quickBarWindow, () => hideQuickBar()),
)
ipcMain.on(
  IPC_QUICKBAR_RUN_ACTIVE,
  from(quickBarWindow, (_e, active) => setQuickBarRunActive(active === true)),
)

ipcMain.on(
  IPC_ORB_CLICK_THROUGH,
  from(orbWindow, (_e, enabled) => setOrbClickThrough(enabled !== false)),
)
ipcMain.on(
  IPC_ORB_EXPANDED,
  from(orbWindow, (_e, expanded) => setOrbExpanded(expanded === true)),
)
ipcMain.on(
  IPC_ORB_INTERFACE_EXPANDED,
  from(orbWindow, (_e, expanded) => setOrbInterfaceExpanded(expanded === true)),
)
ipcMain.on(
  IPC_ORB_DRAG,
  from(orbWindow, (_e, phase) => {
    if (phase === 'start' || phase === 'move' || phase === 'end') orbDrag(phase as OrbDragPhase)
  }),
)
ipcMain.on(
  IPC_CONTEXT_MENU,
  from(orbWindow, () => {
    const win = orbWindow()
    const menu = getAppMenu()
    if (win && menu) menu.popup({ window: win })
  }),
)

ipcMain.on(
  IPC_SHOW_NOTIFICATION,
  (_e, { title, body, contextId }: { title: string; body: string; contextId?: string }) => {
    showWindowsNotification(title, body, async () => {
      let snapshot: ContextSnapshot | null = null
      if (contextId) {
        snapshot = await sidecarRequest<ContextSnapshot>('GET', `/context/${contextId}`)
      }
      openQuickBar(snapshot, 'explain')
    })
  },
)

sidecar.on('ready', (info) => {
  console.log(`[sidecar] ready at ${info.baseUrl}`)
  void syncSettingsState()
})
sidecar.on('sidecar-failed', (err) => {
  console.error(`[sidecar] failed: ${err.message}`)
})

void app.whenReady().then(() => {
  sidecar.start()
  createTray(menuActions)
  createPanelWindow()
  createQuickBarWindow()
  createOrbWindow()
  registerHotkeys(hotkeyActions)
})

app.on('second-instance', () => openQuickBar())

// The orb is always there; the app lives until Quit (orb menu / tray).
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('will-quit', () => {
  unregisterHotkeys()
  sidecar.stop()
})
