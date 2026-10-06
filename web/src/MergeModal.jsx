import { useEffect, useMemo, useState } from 'react'

import { api } from './api'
import { fmtInt, Spinner } from './ui'

// Modal for manually merging author identities. Members/canonical are the
// repo's post-mailmap "base keys" — the same keys the metrics engine uses.
export default function MergeModal({ repo, authors, toast, onClose, onSaved }) {
  const [loading, setLoading] = useState(true)
  const [groups, setGroups] = useState([]) // edited groups [{canonical, members[]}]
  const [selected, setSelected] = useState([]) // keys checked in the picker
  const [saving, setSaving] = useState(false)
  const [q, setQ] = useState('')

  useEffect(() => {
    let alive = true
    api
      .merges(repo.id)
      .then((r) => {
        if (alive) setGroups(r.groups || [])
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [repo.id])

  // Available identities = current canonical authors + member-only keys from
  // existing groups (which have no author row until unmerged).
  const rows = useMemo(() => {
    const byKey = new Map()
    ;(authors || []).forEach((a) =>
      byKey.set(a.key, { key: a.key, name: a.name, email: a.email, commits: a.commits }),
    )
    groups.forEach((g) =>
      g.members.forEach((m) => {
        if (!byKey.has(m)) byKey.set(m, { key: m, name: m, email: '', commits: null })
      }),
    )
    const list = [...byKey.values()]
    list.sort((a, b) => (b.commits || 0) - (a.commits || 0) || a.key.localeCompare(b.key))
    return list
  }, [authors, groups])

  const groupedOf = useMemo(() => {
    const m = new Map()
    groups.forEach((g) => g.members.forEach((k) => m.set(k, g.canonical)))
    return m
  }, [groups])

  const needle = q.trim().toLowerCase()
  const shown = rows.filter(
    (r) => !needle || r.key.toLowerCase().includes(needle) || (r.email || '').includes(needle),
  )

  const toggle = (key) => {
    if (groupedOf.has(key)) return
    setSelected((s) => (s.includes(key) ? s.filter((k) => k !== key) : [...s, key]))
  }

  const createGroup = () => {
    if (selected.length < 2) return
    setGroups((gs) => [...gs, { canonical: selected[0], members: [...selected] }])
    setSelected([])
  }

  const removeMember = (gi, key) => {
    setGroups((gs) =>
      gs
        .map((g, i) => {
          if (i !== gi) return g
          const members = g.members.filter((m) => m !== key)
          const canonical = members.includes(g.canonical) ? g.canonical : members[0]
          return { canonical, members }
        })
        .filter((g) => g.members.length > 0),
    )
  }

  const setCanonical = (gi, value) =>
    setGroups((gs) => gs.map((g, i) => (i === gi ? { ...g, canonical: value } : g)))

  async function save() {
    setSaving(true)
    try {
      await api.setMerges(repo.id, groups)
      onSaved()
    } catch (e) {
      toast('Merge save failed: ' + e.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="modal">
        <div className="modal-head">
          <h3>Merge authors — {repo.name}</h3>
          <button className="btn tiny" type="button" onClick={onClose} title="Close">
            ×
          </button>
        </div>

        <div className="modal-body">
          {loading ? (
            <Spinner label="Loading merge groups…" />
          ) : (
            <>
              {groups.length > 0 && (
                <div style={{ display: 'grid', gap: 10 }}>
                  {groups.map((g, gi) => (
                    <div className="group-box" key={gi}>
                      <div className="row small" style={{ gap: 8 }}>
                        <span className="filter-label">Canonical</span>
                        <select
                          className="input"
                          style={{ maxWidth: 340 }}
                          value={g.canonical}
                          onChange={(e) => setCanonical(gi, e.target.value)}
                        >
                          {g.members.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                        </select>
                        <span className="spacer" />
                        <button
                          className="btn tiny danger"
                          type="button"
                          onClick={() => setGroups((gs) => gs.filter((_, i) => i !== gi))}
                        >
                          Remove group
                        </button>
                      </div>
                      <div className="group-members">
                        {g.members.map((m) => (
                          <span className="chip" key={m} title={m}>
                            {m}
                            <button
                              type="button"
                              title="Remove member"
                              onClick={() => removeMember(gi, m)}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div>
                <div className="row small" style={{ justifyContent: 'space-between' }}>
                  <span className="filter-label">
                    Identities ({fmtInt(selected.length)} selected)
                  </span>
                  <button
                    className="btn tiny primary"
                    type="button"
                    disabled={selected.length < 2}
                    onClick={createGroup}
                  >
                    Group selected ({selected.length})
                  </button>
                </div>
                <input
                  className="input"
                  style={{ margin: '8px 0' }}
                  placeholder="Search authors by name or email…"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
                <div className="pick-list">
                  {shown.map((r) => {
                    const canon = groupedOf.get(r.key)
                    return (
                      <label key={r.key} className="commit-row" style={canon ? { opacity: 0.6 } : undefined}>
                        <input
                          type="checkbox"
                          disabled={!!canon}
                          checked={selected.includes(r.key)}
                          onChange={() => toggle(r.key)}
                        />
                        <span className="commit-subject" title={r.key}>
                          {r.name || r.key}
                          {r.email && <span className="muted"> &lt;{r.email}&gt;</span>}
                          {canon && (
                            <span className="badge busy" style={{ marginLeft: 8 }}>
                              in group
                            </span>
                          )}
                        </span>
                        <span className="muted small">
                          {r.commits == null ? '—' : fmtInt(r.commits)}
                        </span>
                      </label>
                    )
                  })}
                  {!shown.length && (
                    <p className="muted small" style={{ padding: '8px 10px' }}>
                      No authors match.
                    </p>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        <div className="modal-foot">
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" type="button" onClick={save} disabled={saving || loading}>
            {saving ? 'Saving…' : 'Save merges'}
          </button>
        </div>
      </div>
    </div>
  )
}
