/**
 * Reconnecting WebSocket client (PLAN §6.6, §7). Re-asks the preload for the backend on every
 * attempt, because a restarted sidecar comes back on a new port.
 */
import type { ClientMessage, ServerMessage, ServerMessageOf, ServerMessageType } from './protocol'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'failed'

const BACKOFF_MS = [250, 500, 1_000, 2_000, 4_000, 5_000]
const AUTH_FAILED_CODE = 4401

type AnyHandler = (msg: ServerMessage) => void

export class WsClient {
  private ws: WebSocket | null = null
  private attempt = 0
  private stopped = false
  private timer: ReturnType<typeof setTimeout> | null = null
  private readonly handlers = new Map<ServerMessageType, Set<AnyHandler>>()
  private readonly statusHandlers = new Set<(s: ConnectionStatus, detail?: string) => void>()
  private _status: ConnectionStatus = 'disconnected'

  get status(): ConnectionStatus {
    return this._status
  }

  start(): void {
    this.stopped = false
    if (!this.ws && !this.timer) void this.connect()
  }

  stop(): void {
    this.stopped = true
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.ws?.close()
    this.ws = null
  }

  on<T extends ServerMessageType>(type: T, handler: (msg: ServerMessageOf<T>) => void): () => void {
    let set = this.handlers.get(type)
    if (!set) this.handlers.set(type, (set = new Set()))
    const h = handler as AnyHandler
    set.add(h)
    return () => set.delete(h)
  }

  onStatus(handler: (s: ConnectionStatus, detail?: string) => void): () => void {
    this.statusHandlers.add(handler)
    handler(this._status)
    return () => this.statusHandlers.delete(handler)
  }

  /** Returns false when not connected (the message is dropped, not queued). */
  send(msg: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN || this._status !== 'connected') return false
    this.ws.send(JSON.stringify(msg))
    return true
  }

  private setStatus(s: ConnectionStatus, detail?: string): void {
    this._status = s
    for (const h of this.statusHandlers) h(s, detail)
  }

  private async connect(): Promise<void> {
    this.timer = null
    if (this.stopped) return
    this.setStatus('connecting')

    let info
    try {
      info = await window.clickachu.backend()
    } catch (err) {
      // The sidecar gave up restarting; nothing to reconnect to.
      this.setStatus('failed', err instanceof Error ? err.message : String(err))
      return
    }
    if (this.stopped) return

    const ws = new WebSocket(`${info.baseUrl.replace(/^http/, 'ws')}/ws`)
    this.ws = ws
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: info.token } satisfies ClientMessage))
      this.attempt = 0
      this.setStatus('connected')
    }
    ws.onmessage = (ev: MessageEvent<string>) => {
      let msg: ServerMessage
      try {
        msg = JSON.parse(ev.data) as ServerMessage
      } catch {
        console.warn('[ws] non-JSON message dropped')
        return
      }
      for (const h of this.handlers.get(msg.type) ?? []) h(msg)
    }
    ws.onclose = (ev) => {
      if (this.ws !== ws) return
      this.ws = null
      if (ev.code === AUTH_FAILED_CODE) console.warn('[ws] auth rejected; refetching backend')
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped) return
    this.setStatus('disconnected')
    const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)]
    this.attempt += 1
    this.timer = setTimeout(() => void this.connect(), delay)
  }
}

/** One client per renderer page. */
export const ws = new WsClient()
