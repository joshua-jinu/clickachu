/**
 * Hotkey → capture → show (PLAN §9.2 open sequence). The capture must finish BEFORE the bar takes
 * focus, because the sidecar reads whatever window is in the foreground when it runs.
 */
import type { ContextSnapshot } from '../../src/shared/protocol'
import type { Sidecar } from './sidecar'
import { openQuickBar, quickBarWindow } from './windows/quickbar'

/** The sidecar's own budget is 3 s; give up a little after so the bar still opens. */
const CAPTURE_TIMEOUT_MS = 3_500

let capturing = false

export async function captureContext(sidecar: Sidecar): Promise<ContextSnapshot | null> {
  const signal = AbortSignal.timeout(CAPTURE_TIMEOUT_MS)
  try {
    const { baseUrl, token } = await Promise.race([
      sidecar.backend(),
      new Promise<never>((_, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason), { once: true }),
      ),
    ])
    const res = await fetch(`${baseUrl}/context/capture`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      signal,
    })
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
    return (await res.json()) as ContextSnapshot
  } catch (err) {
    console.warn(`[capture] no context: ${err instanceof Error ? err.message : String(err)}`)
    return null
  }
}

export function createOpenWithContext(sidecar: Sidecar): () => void {
  return () => {
    // A visible HUD may be behind the source app. Capture before taking focus, and never
    // steal focus while an earlier capture is still reading that app's selection.
    if (capturing) return
    const bar = quickBarWindow()
    if (bar?.isFocused()) {
      bar.focus()
      return
    }
    capturing = true
    void captureContext(sidecar)
      .then((context) => openQuickBar(context, undefined, false))
      .finally(() => {
        capturing = false
      })
  }
}
