import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@shared/theme.css'
import { bindStore } from '@shared/store'
import { ws } from '@shared/ws'
import { Orb } from './Orb'

bindStore(ws)
ws.start()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Orb />
  </StrictMode>,
)
