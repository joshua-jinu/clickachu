/// <reference types="vite/client" />

import type { ClickachuBridge } from './bridge'

declare global {
  interface Window {
    clickachu: ClickachuBridge
  }
}

export {}
