import type { ClipMasterApi } from './index'

declare global {
  interface Window {
    clipmaster: ClipMasterApi
  }
}

export {}
