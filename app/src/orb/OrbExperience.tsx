import { useCallback, useEffect, useState } from 'react'
import { useRef } from 'react'
import { QuickBar } from '../quickbar/QuickBar'
import { Orb } from './Orb'
import type { OrbScene } from './orbScene'

export function OrbExperience() {
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [alignment, setAlignment] = useState<'left' | 'right'>('left')
  const [voiceListening, setVoiceListening] = useState(false)
  const orbSceneRef = useRef<OrbScene | null>(null)
  const reportListening = useCallback((listening: boolean) => setVoiceListening(listening), [])
  const expand = (open: boolean) => {
    setVoiceOpen(open)
    window.clickachu.setOrbInterfaceExpanded(open)
  }

  useEffect(() => window.clickachu.onOrbInterfaceLayout((layout) => {
    setVoiceOpen(layout.expanded)
    if (layout.side) setAlignment(layout.side)
  }), [])

  return (
    <div className={voiceOpen
      ? `ck-orb-expanded-layout ${alignment === 'right' ? 'is-anchored-right' : ''}`
      : 'relative h-full w-full'}>
      <Orb expanded={voiceOpen} listening={voiceListening} sceneHandleRef={orbSceneRef} onActivate={() => expand(true)} />
      <div className={voiceOpen ? 'ck-orb-expanded-composer' : 'pointer-events-none invisible absolute inset-0'}>
        <QuickBar inline active={voiceOpen} onClose={() => expand(false)} onListeningChange={reportListening} voiceSceneRef={orbSceneRef} />
      </div>
    </div>
  )
}
