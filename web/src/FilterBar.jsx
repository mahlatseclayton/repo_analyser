import { useEffect, useMemo, useRef, useState } from 'react'

import { api } from './api'
import { fmtInt } from './ui'

// ---- generic dropdown (closes on outside click) ----------------------------

function Dropdown({ label, children, width = 320 }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div className="menu-wrap" ref={ref}>
      <button className="btn" type="button" onClick={() => setOpen((o) => !o)}>
        {label} ▾
      </button>
      {open && (
        <div className="menu" style={{ width }}>
          {children}
        </div>
      )}
    </div>
  )
}

// ---- author multi-select ----------------------------------------------------

function AuthorPicker({ authors, selected, onChange }) {
  const [q, setQ] = useState('')
  const needle = q.trim().toLowerCase()
  const shown = useMemo(
    () =>
      (authors || []).filter(
        (a) => !needle || a.key.toLowerCase().includes(needle) || (a.email || '').includes(needle),
      ),
    [authors, needle],
  )
  const sel = useMemo(() => new Set(selected), [selected])
  const allShown = shown.length > 0 && shown.every((a) => sel.has(a.key))

  const toggle = (key) =>
    onChange(sel.has(key) ? selected.filter((k) => k !== key) : [...selected, key])

  const selectShown = () => {
    if (allShown) onChange(selected.filter((k) => !shown.some((a) => a.key === k)))
    else onChange([...new Set([...selected, ...shown.map((a) => a.key)])])
  }

  const label = selected.length
    ? `${selected.length} author${selected.length === 1 ? '' : 's'}`
    : 'All authors'

  return (
    <Dropdown label={label}>
      <input
        className="input"
        placeholder="Search authors…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="row small" style={{ justifyContent: 'space-between', margin: '8px 2px' }}>
        <span className="muted">
          {fmtInt(shown.length)} of {fmtInt((authors || []).length)}
        </span>
        <span className="row" style={{ gap: 6 }}>
          <button className="btn tiny" type="button" onClick={selectShown} disabled={!shown.length}>
            {allShown ? 'Deselect shown' : 'Select shown'}
          </button>
          {selected.length > 0 && (
            <button className="btn tiny" type="button" onClick={() => onChange([])}>
              Clear all
            </button>
          )}
        </span>
      </div>
      <div className="menu-list">
        {shown.map((a) => (
          <label key={a.key} className="menu-item">
            <input type="checkbox" checked={sel.has(a.key)} onChange={() => toggle(a.key)} />
            <span className="grow" title={a.key}>
              {a.name || a.key}
              {a.email && <span className="muted"> &lt;{a.email}&gt;</span>}
            </span>
            <span className="muted small">{fmtInt(a.commits)}</span>
          </label>
        ))}
        {!shown.length && <p className="muted small">No authors match.</p>}
      </div>
    </Dropdown>
  )
}

// ---- path picker ------------------------------------------------------------

function PathPicker({ tree, value, onChange }) {
  const [q, setQ] = useState('')
  const needle = q.trim().toLowerCase()
  const shown = useMemo(
    () =>
      (tree || [])
        .filter((t) => !needle || t.path.toLowerCase().includes(needle))
        .slice(0, 300),
    [tree, needle],
  )

  return (
    <Dropdown label={value ? value : 'Repository (root)'} width={380}>
      <input
        className="input"
        placeholder="Search files & directories…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="menu-list">
        <div
          className={'menu-item' + (value === '' ? ' active' : '')}
          onClick={() => onChange('')}
          title="Repository root (everything)"
        >
          <span className="grow">Repository (root)</span>
        </div>
        {shown.map((t) => (
          <div
            key={t.path}
            className={'menu-item' + (value === t.path ? ' active' : '')}
            onClick={() => onChange(t.path)}
            title={t.path}
          >
            <span className="grow">{t.path}</span>
            <span className="muted small">{t.type}</span>
          </div>
        ))}
        {!shown.length && <p className="muted small">No paths match.</p>}
      </div>
    </Dropdown>
  )
}

// ---- manual commit selection ------------------------------------------------

function ManualPicker({ repoId, selected, onChange }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    const t = window.setTimeout(async () => {
      try {
        const r = await api.commits(repoId, q, 500)
        if (alive) {
          setRows(r.commits || [])
          setTotal(r.total || 0)
        }
      } catch (e) {
        if (alive) setRows([])
      } finally {
        if (alive) setLoading(false)
      }
    }, 200)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [repoId, q])

  const sel = useMemo(() => new Set(selected), [selected])
  const allShown = rows.length > 0 && rows.every((c) => sel.has(c.hash))

  const toggle = (hash) =>
    onChange(sel.has(hash) ? selected.filter((h) => h !== hash) : [...selected, hash])

  const selectShown = () => {
    if (allShown) onChange(selected.filter((h) => !rows.some((c) => c.hash === h)))
    else onChange([...new Set([...selected, ...rows.map((c) => c.hash)])])
  }

  return (
    <div className="commit-panel">
      <div className="row" style={{ gap: 8 }}>
        <input
          className="input"
          style={{ maxWidth: 340 }}
          placeholder="Search commits by subject, hash or author…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="btn tiny" type="button" onClick={selectShown} disabled={!rows.length}>
          {allShown ? 'Deselect shown' : 'Select all shown'}
        </button>
        {selected.length > 0 && (
          <button className="btn tiny" type="button" onClick={() => onChange([])}>
            Clear selection
          </button>
        )}
        <span className="muted small">
          {loading ? 'Searching…' : `${fmtInt(rows.length)} of ${fmtInt(total)} commits`}
          {selected.length > 0 && ` · ${fmtInt(selected.length)} selected`}
        </span>
      </div>
      <div className="commit-list">
        {rows.map((c) => (
          <label key={c.hash} className="commit-row">
            <input type="checkbox" checked={sel.has(c.hash)} onChange={() => toggle(c.hash)} />
            <span className="commit-hash">{c.hash.slice(0, 8)}</span>
            <span className="commit-subject" title={c.subject}>
              {c.subject || '(no subject)'}
            </span>
            <span className="muted small" style={{ whiteSpace: 'nowrap' }}>
              {c.author}
            </span>
            <span className="muted small" style={{ whiteSpace: 'nowrap' }}>
              {new Date(c.ts * 1000).toLocaleDateString()}
            </span>
          </label>
        ))}
        {!rows.length && !loading && (
          <p className="muted small" style={{ padding: '8px 10px' }}>
            No commits match.
          </p>
        )}
      </div>
    </div>
  )
}

// ---- filter bar -------------------------------------------------------------

const MODES = [
  ['all', 'All'],
  ['range', 'Time range'],
  ['list', 'Manual'],
]

export default function FilterBar({ repoId, authors, tree, filter, onChange, onOpenMerge }) {
  const mode = filter.commits.mode || 'all'

  const setCommits = (patch) => onChange({ ...filter, commits: { ...filter.commits, ...patch } })

  const setStartDate = (s) =>
    setCommits({
      startDate: s,
      start: s ? Math.floor(new Date(s + 'T00:00:00').getTime() / 1000) : undefined,
    })

  const setEndDate = (s) =>
    setCommits({
      endDate: s,
      // "To" date is inclusive: end = the following midnight ([start, end) semantics).
      end: s ? Math.floor(new Date(s + 'T00:00:00').getTime() / 1000) + 86400 : undefined,
    })

  return (
    <div className="filterbar">
      <div className="filter-row">
        <span className="filter-label">Filter</span>
        <AuthorPicker authors={authors} selected={filter.authorKeys} onChange={(keys) => onChange({ ...filter, authorKeys: keys })} />
        <PathPicker tree={tree} value={filter.path} onChange={(path) => onChange({ ...filter, path })} />

        <span className="filter-label" style={{ marginLeft: 8 }}>
          Commits
        </span>
        <div className="seg">
          {MODES.map(([m, label]) => (
            <button
              key={m}
              type="button"
              className={mode === m ? 'active' : ''}
              onClick={() => setCommits({ mode: m })}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === 'range' && (
          <>
            <label className="date-field">
              From
              <input
                type="date"
                className="input date"
                value={filter.commits.startDate || ''}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </label>
            <label className="date-field">
              To
              <input
                type="date"
                className="input date"
                value={filter.commits.endDate || ''}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </label>
            {(filter.commits.startDate || filter.commits.endDate) && (
              <button
                className="btn tiny"
                type="button"
                onClick={() => setCommits({ startDate: '', endDate: '', start: undefined, end: undefined })}
              >
                Clear dates
              </button>
            )}
          </>
        )}

        <span className="spacer" />
        {onOpenMerge && (
          <button className="btn" type="button" onClick={onOpenMerge}>
            Merge authors
          </button>
        )}
      </div>

      {mode === 'list' && (
        <ManualPicker
          repoId={repoId}
          selected={filter.commits.hashes || []}
          onChange={(hashes) => setCommits({ hashes })}
        />
      )}
    </div>
  )
}
