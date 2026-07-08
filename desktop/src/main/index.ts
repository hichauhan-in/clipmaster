import { app, BrowserWindow, ipcMain, dialog, shell } from 'electron'
import { spawn, ChildProcessWithoutNullStreams } from 'child_process'
import { join } from 'path'

// --- Backend (Python sidecar) configuration ---------------------------------
// The desktop app talks to the local ClipMaster API. In development you can run
// the server yourself (`clipmaster serve`) and set CLIPMASTER_NO_SIDECAR=1 so
// this process doesn't spawn a second one.
const BACKEND_HOST = process.env.CLIPMASTER_SERVER_HOST ?? '127.0.0.1'
const BACKEND_PORT = process.env.CLIPMASTER_SERVER_PORT ?? '8756'
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`
const SPAWN_SIDECAR = process.env.CLIPMASTER_NO_SIDECAR !== '1'
const PYTHON_BIN = process.env.CLIPMASTER_PYTHON ?? 'python'

let backend: ChildProcessWithoutNullStreams | null = null
let mainWindow: BrowserWindow | null = null

function startBackend(): void {
  if (!SPAWN_SIDECAR) {
    console.log('[clipmaster] sidecar disabled; expecting a server at', BACKEND_URL)
    return
  }
  // The repo root is the parent of the desktop app folder (dev) / app path.
  const repoRoot = join(app.getAppPath(), '..')
  console.log('[clipmaster] starting backend:', PYTHON_BIN, '-m clipmaster.server')
  backend = spawn(PYTHON_BIN, ['-m', 'clipmaster.server'], {
    cwd: repoRoot,
    env: {
      ...process.env,
      CLIPMASTER_SERVER_HOST: BACKEND_HOST,
      CLIPMASTER_SERVER_PORT: BACKEND_PORT
    }
  })
  backend.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`))
  backend.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`))
  backend.on('exit', (code) => console.log('[clipmaster] backend exited', code))
}

function stopBackend(): void {
  if (backend && !backend.killed) {
    backend.kill()
    backend = null
  }
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 940,
    minHeight: 640,
    show: false,
    backgroundColor: '#0b0d10',
    title: 'ClipMaster',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true
    }
  })

  mainWindow.on('ready-to-show', () => mainWindow?.show())

  // electron-vite exposes the renderer dev server URL during `npm run dev`.
  const devUrl = process.env['ELECTRON_RENDERER_URL']
  if (devUrl) {
    mainWindow.loadURL(devUrl)
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// --- IPC ---------------------------------------------------------------------
ipcMain.handle('app:getBackendUrl', () => BACKEND_URL)

ipcMain.handle('dialog:openVideo', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Select a video',
    properties: ['openFile'],
    filters: [
      { name: 'Video', extensions: ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'flv'] },
      { name: 'All files', extensions: ['*'] }
    ]
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths[0]
})

ipcMain.handle('shell:openPath', async (_e, path: string) => shell.openPath(path))

// --- Lifecycle ---------------------------------------------------------------
app.whenReady().then(() => {
  startBackend()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('will-quit', stopBackend)
