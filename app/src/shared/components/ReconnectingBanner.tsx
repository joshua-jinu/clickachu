import type { ConnectionStatus } from '../ws'

interface ReconnectingBannerProps {
  status: ConnectionStatus
  detail?: string
  className?: string
}

export function ReconnectingBanner({ status, detail, className = '' }: ReconnectingBannerProps) {
  if (status === 'connected') return null

  let text = 'Connecting to Clickachu sidecar…'
  let bg = 'bg-amber-500/15 border-amber-500/30 text-amber-200'

  if (status === 'connecting') {
    text = 'Reconnecting to Clickachu sidecar…'
  } else if (status === 'disconnected') {
    text = 'Disconnected from Clickachu sidecar — attempting to reconnect…'
  } else if (status === 'failed') {
    text = detail || 'Sidecar connection failed — check logs or restart Clickachu.'
    bg = 'bg-red-500/15 border-red-500/30 text-red-200'
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-center justify-between gap-2 border-b px-3 py-1.5 text-xs font-medium ${bg} ${className}`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={`inline-block h-2 w-2 shrink-0 rounded-full ${
            status === 'failed' ? 'bg-red-400' : 'animate-pulse bg-amber-400'
          }`}
        />
        <span className="truncate">{text}</span>
      </div>
    </div>
  )
}
