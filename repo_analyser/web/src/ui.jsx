import { useMemo, useState } from 'react'

// ---- number formatting -----------------------------------------------------

export const fmtInt = (n) => (typeof n === 'number' ? n.toLocaleString() : '—')
export const fmt2 = (n) => (typeof n === 'number' ? n.toFixed(2) : '—')
export const fmtPct = (n) => (typeof n === 'number' ? (n * 100).toFixed(1) + '%' : '—')

// ---- small components ------------------------------------------------------

const STATUS_KIND = {
  ready: 'ok',
  cloning: 'busy',
  extracting: 'busy',
  indexing: 'busy',
  error: 'bad',
}

export function StatusBadge({ status }) {
  const kind = STATUS_KIND[status] || 'busy'
  return <span className={'badge ' + kind}>{status}</span>
}

export function Toasts({ items, dismiss }) {
  if (!items || !items.length) return null
  return (
    <div className="toasts">
      {items.map((t) => (
        <div key={t.id} className={'toast ' + t.type} onClick={() => dismiss(t.id)}>
          {t.text}
        </div>
      ))}
    </div>
  )
}

export function Spinner({ label }) {
  return (
    <div className="spinner-row">
      <span className="spinner" />
      {label && <span className="muted">{label}</span>}
    </div>
  )
}

// ---- sortable table --------------------------------------------------------

export function SortableTable({ columns, rows, initialSort, emptyText = 'No rows.' }) {
  const [sort, setSort] = useState(initialSort || (columns[0] ? { key: columns[0].key, dir: -1 } : null))

  const sorted = useMemo(() => {
    const arr = [...(rows || [])]
    if (!sort) return arr
    const col = columns.find((c) => c.key === sort.key)
    if (!col) return arr
    arr.sort((a, b) => {
      const va = col.sortValue ? col.sortValue(a) : a[col.key]
      const vb = col.sortValue ? col.sortValue(b) : b[col.key]
      if (typeof va === 'string' || typeof vb === 'string') {
        return sort.dir * String(va).localeCompare(String(vb))
      }
      return sort.dir * ((va || 0) - (vb || 0))
    })
    return arr
  }, [rows, columns, sort])

  if (!rows || !rows.length) return <p className="muted">{emptyText}</p>

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={
                  (c.align === 'right' ? 'right ' : '') +
                  (sort && sort.key === c.key ? 'sorted' : '')
                }
                onClick={() =>
                  setSort((s) =>
                    s && s.key === c.key ? { key: c.key, dir: -s.dir } : { key: c.key, dir: -1 },
                  )
                }
              >
                {c.label}
                {sort && sort.key === c.key ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={r.key || r.path || i}>
              {columns.map((c) => (
                <td key={c.key} className={c.align === 'right' ? 'right' : ''}>
                  {c.render ? c.render(r) : c.fmt ? c.fmt(r[c.key]) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
