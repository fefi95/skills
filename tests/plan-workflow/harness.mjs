// Runs a generated workflow in Node with fake runtime globals (no real agents).
import { readFileSync } from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

/**
 * @param {string} file generated workflow
 * @param {(call: {prompt: string, label: string, opts: object, n: number}) => any} handler fake agent
 * @param {*} args value for the `args` global
 */
export async function runWorkflow(file, handler, args) {
  const src = readFileSync(file, 'utf8')
  if (!src.startsWith('export const meta')) throw new Error('meta must be the first statement')
  if (/\bimport\s*\(/.test(src)) throw new Error('import() is not allowed')
  const body = src.replace(/^export const meta/, 'const meta')
  const calls = []
  const logs = []
  let active = 0
  let maxActive = 0
  const agent = async (prompt, opts = {}) => {
    const call = { prompt, label: opts.label || '', opts, n: calls.length }
    calls.push(call)
    active++
    maxActive = Math.max(maxActive, active)
    try {
      await new Promise((r) => setTimeout(r, 1))
      const out = await handler(call)
      if (out === undefined) throw new Error(`no fake answer for "${call.label}"`)
      return out
    } finally {
      active--
    }
  }
  // Like the real runtime: inside pipeline a failed agent becomes null.
  const pipeline = (items, fn) => Promise.all(items.map((it, i) => Promise.resolve().then(() => fn(it, i)).catch(() => null)))
  const parallel = (tasks) => Promise.all(tasks.map((t) => Promise.resolve().then(t).catch(() => null)))
  const fn = new AsyncFunction('agent', 'pipeline', 'parallel', 'phase', 'log', 'args', 'Date', 'Math', body)
  const noClock = { now() { throw new Error('Date.now is not allowed') } }
  const noRandom = { ...Math, random() { throw new Error('Math.random is not allowed') } }
  const result = await fn(agent, pipeline, parallel, () => {}, (m) => logs.push(m), args, noClock, noRandom)
  return { result, calls, logs, get maxActive() { return maxActive } }
}
