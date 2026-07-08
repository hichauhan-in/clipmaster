import type { HealthResponse, ProjectSummary } from '../types'
import { baseName, formatTime } from '../util'

interface Props {
  view: string
  onNavigate: (view: 'home' | 'diagnostics') => void
  health: HealthResponse | null
  projects: ProjectSummary[]
  activeProjectId: string | null
  onOpenProject: (id: string) => void
}

export function Sidebar({
  view,
  onNavigate,
  health,
  projects,
  activeProjectId,
  onOpenProject
}: Props): JSX.Element {
  const hasIssue = health ? health.components.some((c) => !c.ok) : false
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="logo" />
        <div>
          <h1>ClipMaster</h1>
          <small>{health ? `v${health.version}` : 'connecting…'}</small>
        </div>
      </div>

      <div className="nav">
        <button className={view === 'diagnostics' ? '' : 'active'} onClick={() => onNavigate('home')}>
          + New analysis
        </button>
        <button
          className={view === 'diagnostics' ? 'active' : ''}
          onClick={() => onNavigate('diagnostics')}
        >
          Diagnostics
          {hasIssue && <span className="nav-alert" title="Something needs attention" />}
        </button>
      </div>

      <div className="section-label">Environment</div>
      <div style={{ padding: '0 14px' }}>
        {health ? (
          health.components.map((c) => (
            <div key={c.name} className={`badge ${c.ok ? 'ok' : 'bad'}`} style={{ margin: '3px 3px' }} title={c.detail}>
              <span className="dot" />
              {c.name}
            </div>
          ))
        ) : (
          <div className="empty">Checking…</div>
        )}
      </div>

      <div className="section-label">Projects</div>
      <div className="project-list">
        {projects.length === 0 && <div className="empty" style={{ paddingLeft: 8 }}>No projects yet</div>}
        {projects.map((p) => (
          <div
            key={p.project_id}
            className="project-item"
            style={activeProjectId === p.project_id ? { background: 'var(--bg-elev-2)' } : undefined}
            onClick={() => onOpenProject(p.project_id)}
          >
            <div className="name">{baseName(p.source_path)}</div>
            <div className="meta">
              {formatTime(p.duration_s)} · {p.chapters} ch · {p.clips} clips
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}
