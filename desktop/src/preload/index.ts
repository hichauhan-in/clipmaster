import { contextBridge, ipcRenderer } from 'electron'

// The single, safe surface the renderer is allowed to use. No Node APIs leak in.
const api = {
  /** Resolve the local ClipMaster API base URL (from the main process). */
  getBackendUrl: (): Promise<string> => ipcRenderer.invoke('app:getBackendUrl'),
  /** Open a native file picker and return the chosen video path (or null). */
  selectVideoFile: (): Promise<string | null> => ipcRenderer.invoke('dialog:openVideo'),
  /** Open a native folder picker and return the chosen directory (or null). */
  selectFolder: (): Promise<string | null> => ipcRenderer.invoke('dialog:openFolder'),
  /** Open a file/folder with the OS default handler. */
  openPath: (path: string): Promise<string> => ipcRenderer.invoke('shell:openPath', path),
  /** Open an http(s) URL in the user's default browser. */
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke('shell:openExternal', url)
}

contextBridge.exposeInMainWorld('clipmaster', api)

export type ClipMasterApi = typeof api
