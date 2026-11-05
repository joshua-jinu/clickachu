/**
 * Orb page (PLAN §9.1, §9.4, P8).
 * The window is click-through except over the sphere and hover bubble.
 * Click → toggle assistant workspace, drag > 4 px → move (main snaps on release), right-click → context menu.
 * Shows status badge and hover bubble for suggestions (Investigate / Dismiss / Later).
 */
import { type MutableRefObject, type PointerEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@shared/api'
import type { SuggestionEvent } from '@shared/protocol'
import { useStore } from '@shared/store'
import { ws } from '@shared/ws'
import { OrbScene, type OrbVisualState } from './orbScene'

const SIZE = 120
const RADIUS = 24
const HIT_RADIUS = 48
const DRAG_THRESHOLD = 8
const DIM_AFTER_MS = 5_000
const DIM_OPACITY = 0.4
const ERROR_LINGER_MS = 4_000

type Badge = 'amber' | 'red' | 'violet' | null

const BADGE_CLASS: Record<Exclude<Badge, null>, string> = {
  amber: 'bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.9)]',
  red: 'bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.9)]',
  violet: 'bg-ck-violet shadow-[0_0_6px_rgba(139,92,246,0.9)]',
}

interface Press {
  pointerId: number
  x: number
  y: number
  dragging: boolean
}

export function Orb({
  onActivate,
  isEnabled = true,
  expanded = false,
  listening = false,
  sceneHandleRef,
}: { onActivate?: () => void; isEnabled?: boolean; expanded?: boolean; listening?: boolean; sceneHandleRef?: MutableRefObject<OrbScene | null> }) {
  const orbState = useStore((s) => s.orbState)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sceneRef = useRef<OrbScene | null>(null)
  const pressRef = useRef<Press | null>(null)
  const draggedRef = useRef(false)
  const lastClickRef = useRef(0)
  const hoverRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const orbBoxRef = useRef<HTMLDivElement>(null)

  const [hovered, setHovered] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [dimmed, setDimmed] = useState(false)
  const [errorFaded, setErrorFaded] = useState(false)
  const [suggestion, setSuggestion] = useState<SuggestionEvent | null>(null)
  const [bubbleVisible, setBubbleVisible] = useState(false)

  const hidden = orbState === 'hidden'
  const renderSize = expanded ? 130 : SIZE
  const renderRadius = expanded ? 54 : RADIUS
  const hitRadius = expanded ? 70 : HIT_RADIUS
  const visual: OrbVisualState =
    orbState === 'hidden' || (orbState === 'error' && errorFaded)
      ? 'idle'
      : listening
        ? 'listening'
        : orbState
  const active = hovered || dragging || visual !== 'idle' || Boolean(suggestion)

  const visualRef = useRef(visual)
  const hoveredRef = useRef(hovered)

  // Three.js scene lifecycle
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const scene = new OrbScene(canvas, renderSize, renderRadius)
    sceneRef.current = scene
    let activeScene = scene
    if (sceneHandleRef) sceneHandleRef.current = scene
    scene.start()

    const handleContextLost = (e: Event) => {
      e.preventDefault()
    }
    const handleContextRestored = () => {
      activeScene.dispose()
      const newScene = new OrbScene(canvas, renderSize, renderRadius)
      activeScene = newScene
      sceneRef.current = newScene
      if (sceneHandleRef) sceneHandleRef.current = newScene
      newScene.setState(visualRef.current)
      newScene.setHovered(hoveredRef.current)
      newScene.start()
    }

    canvas.addEventListener('webglcontextlost', handleContextLost, false)
    canvas.addEventListener('webglcontextrestored', handleContextRestored, false)

    return () => {
      canvas.removeEventListener('webglcontextlost', handleContextLost)
      canvas.removeEventListener('webglcontextrestored', handleContextRestored)
      activeScene.dispose()
      if (sceneHandleRef?.current === activeScene) sceneHandleRef.current = null
      sceneRef.current = null
    }
  }, [renderSize, renderRadius, sceneHandleRef])

  useEffect(() => {
    visualRef.current = visual
    sceneRef.current?.setState(visual)
  }, [visual])
  useEffect(() => {
    hoveredRef.current = hovered
    sceneRef.current?.setHovered(hovered)
  }, [hovered])

  // RMS level events while listening (§17)
  useEffect(() => {
    return ws.on('voice.level', (msg) => {
      sceneRef.current?.setLevel(msg.rms)
    })
  }, [])

  // Fullscreen hides the orb (§9.1)
  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    if (hidden || !isEnabled) {
      scene.stop()
      hoverRef.current = false
      if (hidden) {
        window.clickachu.setOrbExpanded(false)
        window.clickachu.setClickThrough(true)
      }
    } else {
      scene.start()
    }
  }, [hidden, isEnabled])

  useEffect(() => {
    if (orbState !== 'error') return
    const t = setTimeout(() => setErrorFaded(true), ERROR_LINGER_MS)
    return () => {
      clearTimeout(t)
      setErrorFaded(false)
    }
  }, [orbState])

  // Idle dimming
  useEffect(() => {
    if (active) return
    const t = setTimeout(() => setDimmed(true), DIM_AFTER_MS)
    return () => {
      clearTimeout(t)
      setDimmed(false)
    }
  }, [active])

  // Suggestions listener (§16)
  useEffect(() => {
    return ws.on('suggestion', (msg) => {
      setSuggestion(msg)
      if (hidden) {
        window.clickachu.showNotification(msg.title, msg.body, msg.context_id)
      }
    })
  }, [hidden])

  const isOverOrb = useCallback((clientX: number, clientY: number): boolean => {
    const rect = orbBoxRef.current?.getBoundingClientRect()
    if (rect && rect.width > 0) {
      const orbCenterX = rect.left + rect.width / 2
      const orbCenterY = rect.top + rect.height / 2
      return Math.hypot(clientX - orbCenterX, clientY - orbCenterY) <= hitRadius
    }
    const el = containerRef.current
    const w = el ? el.clientWidth : SIZE
    const orbCenterX = w - SIZE / 2
    const orbCenterY = SIZE / 2
    return Math.hypot(clientX - orbCenterX, clientY - orbCenterY) <= hitRadius
  }, [hitRadius])

  const isOverBubble = useCallback((clientX: number, clientY: number): boolean => {
    const el = containerRef.current
    const w = el ? el.clientWidth : SIZE
    if (w <= SIZE || !suggestion) return false
    return clientX >= 8 && clientX <= w - SIZE - 12 && clientY >= 12 && clientY <= 108
  }, [suggestion])

  // Hover via forwarded mouse moves
  useEffect(() => {
    const setHover = (h: boolean) => {
      if (hoverRef.current === h) return
      hoverRef.current = h
      setHovered(h)
      window.clickachu.setClickThrough(!h)
    }

    const onMove = (e: MouseEvent) => {
      if (pressRef.current || hidden) return
      const overOrb = isOverOrb(e.clientX, e.clientY)
      const overBubble = isOverBubble(e.clientX, e.clientY)

      if (overOrb && suggestion && !bubbleVisible) {
        setBubbleVisible(true)
        window.clickachu.setOrbExpanded(true)
      } else if (!overOrb && !overBubble && bubbleVisible) {
        setBubbleVisible(false)
        window.clickachu.setOrbExpanded(false)
      }

      setHover(overOrb || overBubble)
    }

    const onLeave = (e: MouseEvent) => {
      if (pressRef.current) return
      if (isOverOrb(e.clientX, e.clientY) || isOverBubble(e.clientX, e.clientY)) return
      setBubbleVisible(false)
      window.clickachu.setOrbExpanded(false)
      setHover(false)
    }

    window.addEventListener('mousemove', onMove)
    document.documentElement.addEventListener('mouseleave', onLeave)
    return () => {
      window.removeEventListener('mousemove', onMove)
      document.documentElement.removeEventListener('mouseleave', onLeave)
    }
  }, [hidden, suggestion, bubbleVisible, isOverOrb, isOverBubble])

  const openOrbHud = useCallback(() => {
    const now = Date.now()
    if (now - lastClickRef.current < 150) return
    lastClickRef.current = now
    setSuggestion(null)
    setBubbleVisible(false)
    window.clickachu.setOrbExpanded(false)
    if (onActivate) onActivate()
    else window.clickachu.openQuickBar(null, undefined, true)
  }, [onActivate])

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0 || !isOverOrb(e.clientX, e.clientY)) return
    draggedRef.current = false
    pressRef.current = { pointerId: e.pointerId, x: e.screenX, y: e.screenY, dragging: false }
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    const press = pressRef.current
    if (!press || press.pointerId !== e.pointerId) return
    if (!press.dragging) {
      if (Math.hypot(e.screenX - press.x, e.screenY - press.y) <= DRAG_THRESHOLD) return
      press.dragging = true
      draggedRef.current = true
      setDragging(true)
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        // pointer capture can fail on non-focusable windows; ignore
      }
      window.clickachu.orbDrag('start')
    }
    window.clickachu.orbDrag('move')
  }

  function onPointerUp(e: PointerEvent<HTMLDivElement>) {
    const press = pressRef.current
    if (!press || press.pointerId !== e.pointerId) return
    pressRef.current = null
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    } catch {
      // ignore
    }
    if (press.dragging) {
      setDragging(false)
      window.clickachu.orbDrag('end')
    } else {
      openOrbHud()
    }
    if (!isOverOrb(e.clientX, e.clientY)) {
      hoverRef.current = false
      setHovered(false)
      window.clickachu.setClickThrough(true)
    }
  }

  function onPointerCancel() {
    if (pressRef.current?.dragging) window.clickachu.orbDrag('end')
    pressRef.current = null
    setDragging(false)
    setBubbleVisible(false)
    window.clickachu.setOrbExpanded(false)
  }

  function handleClick(e: React.MouseEvent) {
    if (draggedRef.current) {
      draggedRef.current = false
      return
    }
    if (isOverOrb(e.clientX, e.clientY)) {
      openOrbHud()
    }
  }

  async function handleInvestigate(e: React.MouseEvent) {
    e.stopPropagation()
    if (!suggestion) return
    const sug = suggestion
    setSuggestion(null)
    setBubbleVisible(false)
    window.clickachu.setOrbExpanded(false)
    window.clickachu.setClickThrough(true)

    ws.send({
      type: 'suggestion.respond',
      suggestion_id: sug.id,
      decision: 'accept',
    })

    let context = null
    if (sug.context_id) {
      try {
        context = await api.getContext(sug.context_id)
      } catch (err) {
        console.error('failed to fetch context for suggestion:', err)
      }
    }
    window.clickachu.openQuickBar(context, 'explain')
  }

  function handleDismiss(e: React.MouseEvent) {
    e.stopPropagation()
    if (!suggestion) return
    ws.send({
      type: 'suggestion.respond',
      suggestion_id: suggestion.id,
      decision: 'dismiss',
    })
    setSuggestion(null)
    setBubbleVisible(false)
    window.clickachu.setOrbExpanded(false)
    window.clickachu.setClickThrough(true)
  }

  function handleLater(e: React.MouseEvent) {
    e.stopPropagation()
    if (!suggestion) return
    ws.send({
      type: 'suggestion.respond',
      suggestion_id: suggestion.id,
      decision: 'later',
    })
    setSuggestion(null)
    setBubbleVisible(false)
    window.clickachu.setOrbExpanded(false)
    window.clickachu.setClickThrough(true)
  }

  const badge: Badge =
    visual === 'attention'
      ? 'amber'
      : visual === 'error'
        ? 'red'
        : suggestion
          ? 'violet'
          : null

  return (
    <div
      ref={containerRef}
      className={expanded
        ? `ck-orb-expanded-visual relative flex h-full w-[194px] shrink-0 select-none items-center justify-center ${listening ? 'is-listening' : ''}`
        : 'relative flex h-full w-full select-none items-center justify-end'}
      onClick={handleClick}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onContextMenu={(e) => {
        e.preventDefault()
        if (isOverOrb(e.clientX, e.clientY)) window.clickachu.showContextMenu()
      }}
      style={{ cursor: hovered ? (dragging ? 'grabbing' : 'pointer') : 'default' }}
    >
      {/* Hover bubble beside the orb (PLAN §9.4, P8) */}
      {bubbleVisible && suggestion && !hidden && (
        <div
          className="mr-3 flex w-[200px] flex-col rounded-xl border border-white/10 bg-ck-card/95 p-2.5 shadow-2xl backdrop-blur-md"
          onMouseEnter={() => window.clickachu.setClickThrough(false)}
        >
          <span className="truncate text-xs font-semibold text-ck-text">{suggestion.title}</span>
          <span className="mt-0.5 line-clamp-2 text-[11px] leading-tight text-ck-muted">
            {suggestion.body}
          </span>
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleInvestigate}
              className="rounded border border-ck-violet/40 bg-ck-violet/20 px-2 py-0.5 text-[10px] font-medium text-ck-text hover:bg-ck-violet/30"
            >
              Investigate
            </button>
            <button
              type="button"
              onClick={handleDismiss}
              className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-ck-muted hover:bg-white/10 hover:text-ck-text"
            >
              Dismiss
            </button>
            <button
              type="button"
              onClick={handleLater}
              className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-ck-muted hover:bg-white/10 hover:text-ck-text"
            >
              Later
            </button>
          </div>
        </div>
      )}

      {/* Orb container */}
      <div ref={orbBoxRef} className={expanded ? 'ck-orb-grown relative h-[160px] w-[160px] shrink-0' : 'relative h-[120px] w-[120px] shrink-0'}>
        {!hidden && (
          <div
            className="
              absolute rounded-full
            "
            style={{
              left: renderSize / 2 - hitRadius,
              top: renderSize / 2 - hitRadius,
              width: hitRadius * 2,
              height: hitRadius * 2,
              backgroundColor: 'rgba(255, 255, 255, 0.01)',
            }}
          />
        )}
        <canvas
          ref={canvasRef}
          width={renderSize}
          height={renderSize}
          className="pointer-events-none absolute inset-0 transition-opacity duration-500"
          style={{
            width: renderSize,
            height: renderSize,
            opacity: hidden ? 0 : dimmed ? DIM_OPACITY : 1,
          }}
        />
        {badge && !hidden && (
          <span
            className={`pointer-events-none absolute h-2 w-2 rounded-full ring-2 ring-black/40 ${BADGE_CLASS[badge]}`}
            style={{ left: renderSize / 2 + renderRadius * 0.62, top: renderSize / 2 - renderRadius * 0.9 }}
          />
        )}
      </div>
    </div>
  )
}
