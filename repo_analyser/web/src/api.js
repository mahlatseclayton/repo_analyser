// Thin wrapper around the Flask API. All responses are JSON; the backend
// sends {"error": "..."} on failure, which we surface as an Error message.

async function request(path, options) {
  let res
  try {
    res = await fetch(path, options)
  } catch (e) {
    throw new Error('Backend unreachable — is the Flask server running?')
  }
  let data = null
  try {
    data = await res.json()
  } catch (e) {
    data = null
  }
  if (!res.ok) {
    throw new Error((data && data.error) || `HTTP ${res.status}`)
  }
  return data
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  listRepos: () => request('/api/repos'),
  addRepoUrl: (url) => request('/api/repos', json({ url })),
  addRepoZip: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/repos', { method: 'POST', body: fd })
  },
  deleteRepo: (id) => request(`/api/repos/${id}`, { method: 'DELETE' }),
  authors: (id) => request(`/api/repos/${id}/authors`),
  mailmap: (id) => request(`/api/repos/${id}/mailmap`),
  merges: (id) => request(`/api/repos/${id}/merges`),
  setMerges: (id, groups) => request(`/api/repos/${id}/merges`, json({ groups })),
  tree: (id) => request(`/api/repos/${id}/tree`),
  commits: (id, q = '', limit = 100) =>
    request(`/api/repos/${id}/commits?q=${encodeURIComponent(q)}&limit=${limit}`),
  metrics: (id, payload) => request(`/api/repos/${id}/metrics`, json(payload)),
}
