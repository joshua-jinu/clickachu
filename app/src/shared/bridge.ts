/**
 * Shape of `window.clickachu`, exposed by electron/preload/index.ts (PLAN §3).
 *
 * Every window gets the same bridge; main checks which window sent each call and ignores
 * calls from the wrong one.
 */

import type { ContextSnapshot, RunAction, RunMode } from './protocol'

/** Where the Python sidecar lives right now; changes when it restarts (PLAN §6.5). */
export interface BackendInfo {
  baseUrl: string
  token: string
}

/** Sent to the quick bar each time it is shown. */
export interface QuickBarShown {
  /** Tallest the card may grow (45 % of the work area, §9.2), in CSS px. */
  maxHeight: number
  /** Captured before the bar took focus; null when opened from the orb/tray or capture failed. */
  context: ContextSnapshot | null
  initialAction?: RunAction
  isListening?: boolean
}

/** Payload when the quick bar hands an agent run or an Ask answer off to the side panel (§9.2, §9.3). */
export interface PanelHandoff {
  /** Omitted when opening the panel with a selection before submitting a run. */
  runId?: string
  question?: string
  conversationId?: string
  contextId?: string | null
  answer?: string
  mode?: RunMode
  /** When true, the 3D Orb HUD stays open and focused while the side panel slides in with details. */
  keepHudOpen?: boolean
}

export type OrbDragPhase = 'start' | 'move' | 'end'
/** Where the quick bar opens: center stage (Alt+Q, tray) or next to the orb (orb click). */
export type QuickBarAnchor = 'center' | 'orb'

export interface ClickachuBridge {
  /** Resolves once the sidecar is READY; rejects if it gave up restarting. */
  backend(): Promise<BackendInfo>

  // ---- any window
  openQuickBar(
    context?: ContextSnapshot | null,
    initialAction?: RunAction,
    isListening?: boolean,
    anchor?: QuickBarAnchor,
  ): void
  togglePanel(): void
  /** Show the side panel directly on the Settings tab. */
  openSettings(): void
  /** Show the side panel on the Chat tab and hand off a run or completed answer (§9.2). */
  openPanelInChat(handoff?: PanelHandoff): void
  showNotification(title: string, body: string, contextId?: string | null): void

  // ---- quick bar (§9.2)
  /** Report the card's natural height; main clamps it to [120, 45 % of work area]. */
  reportQuickBarHeight(height: number): void
  hideQuickBar(): void
  /** While true the bar stays open on blur. */
  setQuickBarRunActive(active: boolean): void
  /** Returns an unsubscribe. */
  onQuickBarShown(handler: (info: QuickBarShown) => void): () => void
  onVoiceTranscript(handler: (text: string) => void): () => void

  // ---- orb (§9.1)
  setClickThrough(enabled: boolean): void
  setOrbExpanded(expanded: boolean): void
  /** Main reads the cursor itself, so no coordinates are passed. */
  orbDrag(phase: OrbDragPhase): void
  showContextMenu(): void

  // ---- side panel (§9.3)
  /** Native folder dialog; null when cancelled. */
  pickFolder(): Promise<string | null>
  hidePanel(): void
  /** Fires each time the panel is shown, so the renderer can replay its slide-in. */
  onPanelShown(handler: () => void): () => void
  /** Fires when Settings is requested from tray/menu. */
  onOpenSettings(handler: () => void): () => void
  /** Fires when the quick bar hands a run or answer off to the panel's Chat tab. */
  onPanelHandoff(handler: (handoff: PanelHandoff) => void): () => void
}

export const IPC_BACKEND = 'clickachu:backend'
export const IPC_OPEN_QUICKBAR = 'clickachu:open-quickbar'
export const IPC_TOGGLE_PANEL = 'clickachu:toggle-panel'
export const IPC_OPEN_PANEL_CHAT = 'clickachu:open-panel-chat'
export const IPC_QUICKBAR_HEIGHT = 'clickachu:quickbar:height'
export const IPC_QUICKBAR_HIDE = 'clickachu:quickbar:hide'
export const IPC_QUICKBAR_RUN_ACTIVE = 'clickachu:quickbar:run-active'
export const IPC_QUICKBAR_SHOWN = 'clickachu:quickbar:shown'
export const IPC_ORB_CLICK_THROUGH = 'clickachu:orb:click-through'
export const IPC_ORB_EXPANDED = 'clickachu:orb:expanded'
export const IPC_ORB_DRAG = 'clickachu:orb:drag'
export const IPC_CONTEXT_MENU = 'clickachu:context-menu'
export const IPC_PICK_FOLDER = 'clickachu:pick-folder'
export const IPC_PANEL_HIDE = 'clickachu:panel:hide'
export const IPC_PANEL_SHOWN = 'clickachu:panel:shown'
export const IPC_PANEL_HANDOFF = 'clickachu:panel:handoff'
export const IPC_SHOW_NOTIFICATION = 'clickachu:show-notification'
export const IPC_OPEN_SETTINGS = 'clickachu:open-settings'
export const IPC_VOICE_TRANSCRIPT = 'clickachu:voice:transcript'
