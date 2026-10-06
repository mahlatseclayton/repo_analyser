import { useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { fmt2, fmtInt, fmtPct, SortableTable } from './ui'

const PIE_COLORS = [
  '#2563eb', '#60a5fa', '#93c5fd', '#1d4ed8', '#38bdf8',
  '#818cf8', '#a5b4fc', '#c7d2fe', '#64748b',
]

const TABS = [
  ['overview', 'Overview'],
  ['files', 'Files & Dirs'],
  ['authors', 'Authors'],
]

export default function Dashboard({ metrics }) {
  const [tab, setTab] = useState('overview')

  return (
    <div>
      <div className="tabs">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={'tab' + (tab === id ? ' active' : '')}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' && <Overview metrics={metrics} />}
      {tab === 'files' && <FilesDirs metrics={metrics} />}
      {tab === 'authors' && <Authors metrics={metrics} />}
    </div>
  )
}

// ---- overview ---------------------------------------------------------------

function Stat({ label, value, accent }) {
  return (
    <div className={'stat' + (accent ? ' accent' : '')}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  )
}

const shorten = (p) => (p.length > 20 ? '…' + p.slice(-19) : p)

function FileTip({ active, payload }) {
  if (!active || !payload || !payload.length) return null
  const r = payload[0].payload
  return (
    <div className="tip">
      <div className="tip-title">{r.path}</div>
      <div>
        added {fmtInt(r.added)} · removed {fmtInt(r.removed)}
      </div>
      <div>
        λ {fmtInt(r.churn)} · n {fmtInt(r.modifications)}
      </div>
    </div>
  )
}

function Overview({ metrics }) {
  const t = metrics.totals
  const series = metrics.series || []
  const topFiles = (metrics.files || []).slice(0, 10) // engine sorts files by churn

  return (
    <>
      <div className="cards">
        <Stat label="Commits |H|" value={fmtInt(metrics.commit_set_size)} accent />
        <Stat label="Added l+" value={fmtInt(t.added)} />
        <Stat label="Removed l−" value={fmtInt(t.removed)} />
        <Stat label="Growth δ" value={fmtInt(t.growth)} />
        <Stat label="Churn λ" value={fmtInt(t.churn)} />
        <Stat label="Modifications n" value={fmtInt(t.modifications)} />
        <Stat label="Frequency η" value={fmt2(t.frequency)} />
        <Stat label="Churn rate ρ" value={fmt2(t.churn_rate)} />
      </div>

      <div className="chart-grid">
        <div className="card">
          <h3>Activity over time</h3>
          {series.length ? (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={series} margin={{ top: 6, right: 8, left: -14, bottom: 0 }}>
                <CartesianGrid stroke="#eef2f7" vertical={false} />
                <XAxis
                  dataKey="bucket"
                  tick={{ fontSize: 11, fill: '#64748b' }}
                  tickLine={false}
                  axisLine={{ stroke: '#e2e8f0' }}
                  minTickGap={28}
                />
                <YAxis tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                <Tooltip />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="added"
                  name="Added"
                  stroke="#2563eb"
                  fill="#2563eb"
                  fillOpacity={0.12}
                  strokeWidth={2}
                  dot={false}
                />
                <Area
                  type="monotone"
                  dataKey="removed"
                  name="Removed"
                  stroke="#94a3b8"
                  fill="#94a3b8"
                  fillOpacity={0.12}
                  strokeWidth={2}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="muted">No activity in this commit set.</p>
          )}
        </div>

        <div className="card">
          <h3>Top files by churn</h3>
          {topFiles.length ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart
                data={topFiles}
                layout="vertical"
                margin={{ top: 4, right: 14, left: 4, bottom: 0 }}
              >
                <CartesianGrid stroke="#eef2f7" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                <YAxis
                  type="category"
                  dataKey="path"
                  width={150}
                  tick={{ fontSize: 11, fill: '#334155' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={shorten}
                />
                <Tooltip cursor={{ fill: 'rgba(37, 99, 235, 0.06)' }} content={<FileTip />} />
                <Bar dataKey="churn" name="Churn (λ)" fill="#2563eb" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="muted">No file changes in this commit set.</p>
          )}
        </div>
      </div>
    </>
  )
}

// ---- files & dirs -----------------------------------------------------------

const NUM_COLS = [
  { key: 'added', label: 'l+', align: 'right', fmt: fmtInt },
  { key: 'removed', label: 'l−', align: 'right', fmt: fmtInt },
  { key: 'growth', label: 'δ', align: 'right', fmt: fmtInt },
  { key: 'churn', label: 'λ', align: 'right', fmt: fmtInt },
  { key: 'modifications', label: 'n', align: 'right', fmt: fmtInt },
  { key: 'frequency', label: 'η', align: 'right', render: (r) => fmt2(r.frequency) },
  { key: 'churn_rate', label: 'ρ', align: 'right', render: (r) => fmt2(r.churn_rate) },
]

function pathCol(label) {
  return {
    key: 'path',
    label,
    render: (r) => <span className="path-cell">{r.path || '(root)'}</span>,
    sortValue: (r) => r.path,
  }
}

function FilesDirs({ metrics }) {
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="card">
        <h3>Files ({fmtInt((metrics.files || []).length)})</h3>
        <SortableTable
          columns={[pathCol('File'), ...NUM_COLS]}
          rows={metrics.files}
          initialSort={{ key: 'churn', dir: -1 }}
          emptyText="No file changes in this commit set."
        />
      </div>
      <div className="card">
        <h3>Directories ({fmtInt((metrics.dirs || []).length)})</h3>
        <SortableTable
          columns={[pathCol('Directory'), ...NUM_COLS]}
          rows={metrics.dirs}
          initialSort={{ key: 'path', dir: 1 }}
          emptyText="No directories in this commit set."
        />
      </div>
    </div>
  )
}

// ---- authors ----------------------------------------------------------------

const AUTHOR_COLS = [
  {
    key: 'name',
    label: 'Author',
    render: (r) => (
      <span>
        {r.name || r.key}
        <br />
        <span className="muted small">{r.email}</span>
      </span>
    ),
    sortValue: (r) => (r.name || r.key).toLowerCase(),
  },
  { key: 'commits', label: 'Commits', align: 'right', fmt: fmtInt },
  { key: 'modifications', label: 'Modifications', align: 'right', fmt: fmtInt },
  { key: 'churn', label: 'Churn (λ)', align: 'right', fmt: fmtInt },
  { key: 'ownership', label: 'Ownership (ω)', align: 'right', render: (r) => fmtPct(r.ownership) },
]

function Authors({ metrics }) {
  const rows = metrics.authors || []

  const pieData = useMemo(() => {
    const top = rows.slice(0, 8)
    const rest = rows.slice(8)
    const data = top.map((r) => ({ name: r.name || r.key, value: r.churn }))
    if (rest.length) {
      data.push({
        name: `Other (${rest.length})`,
        value: rest.reduce((s, r) => s + r.churn, 0),
      })
    }
    return data
  }, [rows])

  return (
    <div className="chart-grid">
      <div className="card">
        <h3>Authors ({fmtInt(rows.length)})</h3>
        <SortableTable
          columns={AUTHOR_COLS}
          rows={rows}
          initialSort={{ key: 'churn', dir: -1 }}
          emptyText="No authors in this commit set."
        />
      </div>
      <div className="card">
        <h3>Ownership by churn</h3>
        {pieData.length ? (
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={pieData}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={95}
                paddingAngle={2}
              >
                {pieData.map((d, i) => (
                  <Cell key={d.name} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(v) => fmtInt(v)} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        ) : (
          <p className="muted">No authors in this commit set.</p>
        )}
      </div>
    </div>
  )
}
