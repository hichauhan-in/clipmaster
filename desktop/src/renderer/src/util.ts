export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`
}

export function humanSize(bytes: number): string {
  let size = bytes
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  for (const unit of units) {
    if (size < 1024 || unit === 'TB') return `${size.toFixed(1)} ${unit}`
    size /= 1024
  }
  return `${size.toFixed(1)} TB`
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

const KIND_COLORS: Record<string, string> = {
  on_topic: '#3fb950',
  off_topic: '#d29922',
  qa: '#58a6ff',
  filler: '#f85149',
  intro: '#a371f7',
  outro: '#a371f7',
  transition: '#8b949e'
}

export function kindColor(kind: string): string {
  return KIND_COLORS[kind] ?? '#8b949e'
}
