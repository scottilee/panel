// Every API path the frontend fetches must be in the Vite dev proxy, or
// the feature works in production and 404s in development.
//
//   node scripts/check_dev_proxy.mjs
//
// POST /benchmark shipped missing from the proxy, so the market button
// would have failed only on localhost:5173 -- the one place it gets
// demoed from.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const cfg = readFileSync(join(here, '../web/vite.config.js'), 'utf8')
const api = readFileSync(join(here, '../web/src/api.js'), 'utf8')

const proxied = new Set([...cfg.matchAll(/'(\/[a-z]+)':\s*'http/g)].map((m) => m[1]))
const fetched = new Set([
  ...[...api.matchAll(/fetch\(\s*[`'"](\/[a-z]+)/g)].map((m) => m[1]),
  ...[...api.matchAll(/EventSource\(\s*[`'"](\/[a-z]+)/g)].map((m) => m[1]),
])

const missing = [...fetched].filter((p) => !proxied.has(p)).sort()
for (const p of [...fetched].sort()) {
  console.log((proxied.has(p) ? 'OK   ' : 'WRONG') + ' ' + p)
}
console.log(missing.length ? `\nnot in the dev proxy: ${missing.join(', ')}` : '\nevery fetched path is proxied')
process.exit(missing.length ? 1 : 0)
