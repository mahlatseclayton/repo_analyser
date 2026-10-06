import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from './api'
import Dashboard from './Dashboard'
import FilterBar from './FilterBar'
import { fmtInt, Spinner, StatusBadge, Toasts } from './ui'

const DEFAULT_FILTER = { authorKeys: [], path: '', commits: { mode: 'all' } }

// ---- app shell -------------------------------------------------------------

export default function App() {
  const [repos, setRepos] = useState(null) // null = first load
  const [listError, setListError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [toasts, setToasts] = useState([])
  const [detail, setDetail] = useState(null) // {authors, tree} of the selected repo
  const [detailLoading, setDetailLoading] = useState(false)
  const [filter, setFilter] = useState(DEFAULT_FILTER)
  const [metrics, setMetrics] = useState(null)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [metricsError, setMetricsError] = useState(null)
  const toastSeq = useRef(0)

  const toast = useCallback((text, type = 'info') => {
    const id = ++toastSeq.current
    setToasts((t) => [...t, { id, text, type }])
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000)
  }, [])

  const refresh = useCallback(async () => {
    try {
      setRepos(await api.listRepos())
      setListError(null)
    } catch (e) {
      setListError(e.message)
      setRepos((r) => r || [])
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Poll the list while any repo is still being ingested (zip / clone).
  const anyBusy = !!repos && repos.some((r) => r.status !== 'ready' && r.status !== 'error')
  useEffect(() => {
    if (!anyBusy) return undefined
    const t = window.setInterval(refresh, 1500)
    return () => window.clearInterval(t)
  }, [anyBusy, refresh])

  // Keep the selection valid as repos are added / removed.
  useEffect(() => {
    if (!repos) return
    if (selectedId && repos.some((r) => r.id === selectedId)) return
    const first = repos.find((r) => r.status === 'ready') || repos[0]
    setSelectedId(first ? first.id : null)
  }, [repos, selectedId])

  const selected = (repos || []).find((r) => r.id === selectedId) || null
  const selId = selected ? selected.id : null
  const selStatus = selected ? selected.status : null

  // Reset filters when switching repos.
  useEffect(() => {
    setFilter(DEFAULT_FILTER)
    setMetrics(null)
    setMetricsError(null)
    setMetricsLoading(false)
  }, [selectedId])

  // Load authors + tree for the selected repo.
  useEffect(() => {
    if (selId == null || selStatus !== 'ready') {
      setDetail(null)
      return undefined
    }
    let alive = true
    setDetailLoading(true)
    Promise.all([api.authors(selId), api.tree(selId)])
      .then(([a, t]) => {
        if (alive) setDetail({ authors: a.authors || [], tree: t.tree || [] })
      })
      .catch((e) => {
        if (alive) toast('Failed to load repo data: ' + e.message, 'error')
      })
      .finally(() => {
        if (alive) setDetailLoading(false)
      })
    return () => {
      alive = false
    }
  }, [selId, selStatus, toast])

  // Fetch metrics for the current filter (debounced).
  useEffect(() => {
    if (selId == null || selStatus !== 'ready') return undefined
    const c = filter.commits || { mode: 'all' }
    if (c.mode === 'list' && !(c.hashes || []).length) {
      setMetrics(null)
      setMetricsError(null)
      return undefined
    }
    let alive = true
    setMetricsLoading(true)
    const t = window.setTimeout(async () => {
      try {
        const m = await api.metrics(selId, {
          path: filter.path || '',
          authorKeys: filter.authorKeys,
          commits: {
            mode: c.mode,
            ...(c.mode === 'range' ? { start: c.start, end: c.end } : {}),
            ...(c.mode === 'list' ? { hashes: c.hashes } : {}),
          },
        })
        if (alive) {
          setMetrics(m)
          setMetricsError(null)
        }
      } catch (e) {
        if (alive) {
          setMetrics(null)
          setMetricsError(e.message)
        }
      } finally {
        if (alive) setMetricsLoading(false)
      }
    }, 250)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [selId, selStatus, filter])

  const handleAdded = useCallback(
    (repo, message) => {
      refresh()
      setSelectedId(repo.id)
      toast(`${message}: ${repo.name}`, 'success')
    },
    [refresh, toast],
  )

  async function handleDelete(repo) {
    if (!window.confirm(`Delete "${repo.name}" and all of its data?`)) return
    try {
      await api.deleteRepo(repo.id)
      toast(`Deleted ${repo.name}`)
      refresh()
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error')
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">RAT</span>
          <span className="brand-name">Repo Analysis Tool</span>
        </div>
        <span className="spacer" />
        {listError ? (
          <span className="badge bad">API offline</span>
        ) : (
          <span className="badge ok">
            {repos ? `${repos.length} repo${repos.length === 1 ? '' : 's'}` : 'connecting…'}
          </span>
        )}
      </header>

      <div className="layout">
        <Sidebar
          repos={repos}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onDelete={handleDelete}
          onAdded={handleAdded}
          toast={toast}
        />
        <main className="main">
          {listError && (
            <div className="banner">
              <span>Backend unreachable — {listError}</span>
              <button className="btn" onClick={refresh}>
                Retry
              </button>
            </div>
          )}
          {!listError && !repos && <Spinner label="Loading repositories…" />}
          {repos && !selected && (
            <div className="empty card">
              <h2>No repositories yet</h2>
              <p className="muted">
                Add one from the sidebar: upload a zip of a repo (including its <code>.git</code>{' '}
                folder) or paste a clone URL.
              </p>
            </div>
          )}
          {selected && (
            <Workspace
              repo={selected}
              detail={detail}
              detailLoading={detailLoading}
              filter={filter}
              onFilterChange={setFilter}
              metrics={metrics}
              metricsLoading={metricsLoading}
              metricsError={metricsError}
              onDelete={handleDelete}
            />
          )}
        </main>
      </div>

      <Toasts items={toasts} dismiss={(id) => setToasts((t) => t.filter((x) => x.id !== id))} />
    </div>
  )
}

// ---- sidebar: repo list + add form -----------------------------------------

function Sidebar({ repos, selectedId, onSelect, onDelete, onAdded, toast }) {
  return (
    <aside className="sidebar">
      <section>
        <h2 className="side-title">Repositories</h2>
        {!repos && <p className="muted small">Loading…</p>}
        {repos && repos.length === 0 && <p className="muted small">Nothing here yet.</p>}
        <ul className="repo-list">
          {(repos || []).map((r) => (
            <li
              key={r.id}
              className={'repo-item' + (r.id === selectedId ? ' active' : '')}
              onClick={() => onSelect(r.id)}
            >
              <div className="repo-row">
                <span className="repo-name" title={r.name}>
                  {r.name}
                </span>
                <span className="row" style={{ gap: 6 }}>
                  <StatusBadge status={r.status} />
                  <button
                    className="btn tiny danger"
                    title="Delete repo"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(r)
                    }}
                  >
                    ×
                  </button>
                </span>
              </div>
              <div className="repo-meta muted">
                <span>{r.source && r.source.type === 'url' ? 'clone' : 'zip'}</span>
                {r.status === 'ready' && r.git_dir && <span>· indexed on demand</span>}
                {r.error && (
                  <span className="error-text" title={r.error}>
                    · error
                  </span>
                )}
              </div>
              {(r.status === 'cloning' || r.status === 'extracting') && (
                <div className="progress">
                  <div className="progress-bar" />
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>
      <AddRepo onAdded={onAdded} toast={toast} />
    </aside>
  )
}

function AddRepo({ onAdded, toast }) {
  const [url, setUrl] = useState('')
  const [adding, setAdding] = useState(false)
  const [drag, setDrag] = useState(false)
  const fileRef = useRef(null)

  async function addUrl(e) {
    e.preventDefault()
    const u = url.trim()
    if (!u || adding) return
    setAdding(true)
    try {
      onAdded(await api.addRepoUrl(u), 'Clone started')
      setUrl('')
    } catch (err) {
      toast('Add failed: ' + err.message, 'error')
    } finally {
      setAdding(false)
    }
  }

  async function addZip(file) {
    if (!file || adding) return
    setAdding(true)
    try {
      onAdded(await api.addRepoZip(file), 'Zip uploaded')
    } catch (err) {
      toast('Zip rejected: ' + err.message, 'error')
    } finally {
      setAdding(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <section>
      <h2 className="side-title">Add repository</h2>
      <form className="add-form" onSubmit={addUrl}>
        <input
          className="input"
          placeholder="https://github.com/…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="btn primary" type="submit" disabled={adding || !url.trim()}>
          Add
        </button>
      </form>
      <div
        className={'dropzone' + (drag ? ' drag' : '')}
        onClick={() => fileRef.current && fileRef.current.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDrag(false)
          addZip(e.dataTransfer.files && e.dataTransfer.files[0])
        }}
      >
        {adding ? 'Working…' : 'Drop a repo .zip here (must contain .git) — or click to browse'}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".zip,application/zip"
        style={{ display: 'none' }}
        onChange={(e) => addZip(e.target.files && e.target.files[0])}
      />
    </section>
  )
}

// ---- workspace (metrics views plug in here) --------------------------------

function Workspace({
  repo,
  detail,
  detailLoading,
  filter,
  onFilterChange,
  metrics,
  metricsLoading,
  metricsError,
  onDelete,
}) {
  const source =
    repo.source && repo.source.type === 'url' ? repo.source.url : (repo.source || {}).filename
  const listModeEmpty = filter.commits.mode === 'list' && !(filter.commits.hashes || []).length

  return (
    <div>
      <div className="page-head">
        <h2>{repo.name}</h2>
        <StatusBadge status={repo.status} />
        <span className="muted small">
          <code>{source}</code>
        </span>
        <span className="spacer" />
        <button className="btn danger" onClick={() => onDelete(repo)}>
          Delete
        </button>
      </div>

      {(repo.status === 'cloning' || repo.status === 'extracting') && (
        <Spinner label="Ingesting repository…" />
      )}

      {repo.status === 'error' && (
        <div className="card error-card">
          <h3>Ingestion failed</h3>
          <p className="error-text">{repo.error}</p>
        </div>
      )}

      {repo.status === 'ready' && detailLoading && !detail && (
        <Spinner label="Loading repo data (one-pass git parse)…" />
      )}

      {repo.status === 'ready' && detail && (
        <>
          <FilterBar
            repoId={repo.id}
            authors={detail.authors}
            tree={detail.tree}
            filter={filter}
            onChange={onFilterChange}
          />
          <Results
            metrics={metrics}
            metricsLoading={metricsLoading}
            metricsError={metricsError}
            listModeEmpty={listModeEmpty}
          />
        </>
      )}
    </div>
  )
}

// ---- metrics results --------------------------------------------------------

function Results({ metrics, metricsLoading, metricsError, listModeEmpty }) {
  if (listModeEmpty) {
    return (
      <div className="card">
        <p className="muted">Manual mode: pick commits in the filter bar to build the commit set.</p>
      </div>
    )
  }
  if (metricsError) {
    return (
      <div className="card error-card">
        <h3>Metrics query failed</h3>
        <p className="error-text">{metricsError}</p>
      </div>
    )
  }
  if (!metrics) return <Spinner label="Computing metrics…" />
  return (
    <>
      <div className="row small muted" style={{ justifyContent: 'flex-end', margin: '0 2px 8px' }}>
        {metricsLoading
          ? 'updating…'
          : `object: ${metrics.object || '(root)'} · |H| = ${fmtInt(metrics.commit_set_size)}`}
      </div>
      <Dashboard metrics={metrics} />
    </>
  )
}
