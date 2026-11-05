import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import {
  type BackendInfo,
  type ClickachuBridge,
  IPC_BACKEND,
  IPC_CONTEXT_MENU,
  IPC_OPEN_PANEL_CHAT,
  IPC_OPEN_QUICKBAR,
  IPC_OPEN_SETTINGS,
  IPC_ORB_CLICK_THROUGH,
  IPC_ORB_DRAG,
  IPC_ORB_EXPANDED,
  IPC_ORB_INTERFACE_EXPANDED,
  IPC_ORB_INTERFACE_LAYOUT,
  IPC_PANEL_HANDOFF,
  IPC_PANEL_HIDE,
  IPC_PANEL_SHOWN,
  IPC_PICK_FOLDER,
  IPC_QUICKBAR_HEIGHT,
  IPC_QUICKBAR_HIDE,
  IPC_QUICKBAR_RUN_ACTIVE,
  IPC_QUICKBAR_SHOWN,
  IPC_SHOW_NOTIFICATION,
  IPC_TOGGLE_PANEL,
  IPC_VOICE_TRANSCRIPT,
  type PanelHandoff,
  type QuickBarShown,
} from '../../src/shared/bridge'

const api: ClickachuBridge = {
  backend: () => ipcRenderer.invoke(IPC_BACKEND) as Promise<BackendInfo>,

  openQuickBar: (context, initialAction, isListening) =>
    ipcRenderer.send(IPC_OPEN_QUICKBAR, { context, initialAction, isListening }),
  togglePanel: () => ipcRenderer.send(IPC_TOGGLE_PANEL),
  openSettings: () => ipcRenderer.send(IPC_OPEN_SETTINGS),
  openPanelInChat: (handoff) => ipcRenderer.send(IPC_OPEN_PANEL_CHAT, handoff),
  showNotification: (title, body, contextId) =>
    ipcRenderer.send(IPC_SHOW_NOTIFICATION, { title, body, contextId }),

  reportQuickBarHeight: (height) => ipcRenderer.send(IPC_QUICKBAR_HEIGHT, height),
  hideQuickBar: () => ipcRenderer.send(IPC_QUICKBAR_HIDE),
  setQuickBarRunActive: (active) => ipcRenderer.send(IPC_QUICKBAR_RUN_ACTIVE, active),
  onQuickBarShown: (handler) => {
    // Never hand the IpcRendererEvent to the page (it exposes `sender`).
    const listener = (_e: IpcRendererEvent, info: QuickBarShown): void => handler(info)
    ipcRenderer.on(IPC_QUICKBAR_SHOWN, listener)
    return () => ipcRenderer.removeListener(IPC_QUICKBAR_SHOWN, listener)
  },
  onVoiceTranscript: (handler) => {
    const listener = (_e: IpcRendererEvent, text: string): void => handler(text)
    ipcRenderer.on(IPC_VOICE_TRANSCRIPT, listener)
    return () => ipcRenderer.removeListener(IPC_VOICE_TRANSCRIPT, listener)
  },

  setClickThrough: (enabled) => ipcRenderer.send(IPC_ORB_CLICK_THROUGH, enabled),
  setOrbExpanded: (expanded) => ipcRenderer.send(IPC_ORB_EXPANDED, expanded),
  setOrbInterfaceExpanded: (expanded) => ipcRenderer.send(IPC_ORB_INTERFACE_EXPANDED, expanded),
  onOrbInterfaceLayout: (handler) => {
    const listener = (_e: IpcRendererEvent, layout: { expanded: boolean; side?: 'left' | 'right' }): void => handler(layout)
    ipcRenderer.on(IPC_ORB_INTERFACE_LAYOUT, listener)
    return () => ipcRenderer.removeListener(IPC_ORB_INTERFACE_LAYOUT, listener)
  },
  orbDrag: (phase) => ipcRenderer.send(IPC_ORB_DRAG, phase),
  showContextMenu: () => ipcRenderer.send(IPC_CONTEXT_MENU),

  pickFolder: () => ipcRenderer.invoke(IPC_PICK_FOLDER) as Promise<string | null>,
  hidePanel: () => ipcRenderer.send(IPC_PANEL_HIDE),
  onPanelShown: (handler) => {
    const listener = (): void => handler()
    ipcRenderer.on(IPC_PANEL_SHOWN, listener)
    return () => ipcRenderer.removeListener(IPC_PANEL_SHOWN, listener)
  },
  onOpenSettings: (handler) => {
    const listener = (): void => handler()
    ipcRenderer.on(IPC_OPEN_SETTINGS, listener)
    return () => ipcRenderer.removeListener(IPC_OPEN_SETTINGS, listener)
  },
  onPanelHandoff: (handler) => {
    const listener = (_e: IpcRendererEvent, handoff: PanelHandoff): void => handler(handoff)
    ipcRenderer.on(IPC_PANEL_HANDOFF, listener)
    return () => ipcRenderer.removeListener(IPC_PANEL_HANDOFF, listener)
  },
}

contextBridge.exposeInMainWorld('clickachu', api)
