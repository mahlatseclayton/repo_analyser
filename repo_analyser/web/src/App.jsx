import { useEffect, useState } from 'react'

export default function App() {
  const [health, setHealth] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          RAT <span className="muted">Repo Analysis Tool</span>
        </h1>
      </header>
      <main className="content">
        <div className="card">
          <h2>Backend status</h2>
          {error && <p className="error">API unreachable: {error}</p>}
          {!error && !health && <p className="muted">Checking…</p>}
          {health && <pre>{JSON.stringify(health, null, 2)}</pre>}
        </div>
      </main>
    </div>
  )
}
