import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWorkflow } from './harness.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const gen = join(root, 'plugins/plan-workflow/skills/plan-workflow/scripts/generate.py')

function generate(yaml, name = 'demo') {
  const dir = mkdtempSync(join(tmpdir(), 'plan-workflow-'))
  const plan = join(dir, `${name}.plan.yaml`)
  const out = join(dir, `${name}.js`)
  writeFileSync(plan, yaml)
  execFileSync('python3', [gen, 'generate', plan, '--out', out], { stdio: 'pipe' })
  const hash = execFileSync('python3', [gen, 'hash', plan]).toString().trim()
  return { out, hash, plan }
}

const diamond = `
plan: {title: Demo}
steps:
  - {id: a, title: A, files: [{path: a.txt, action: create}], acceptance_criteria: [{given: x, when: y, then: z}]}
  - {id: b, title: B, depends_on: [a], acceptance_criteria: [{given: x, when: y, then: z}]}
  - {id: c, title: C, depends_on: [a], acceptance_criteria: [{given: x, when: y, then: z}]}
  - {id: d, title: D, depends_on: [b, c], acceptance_criteria: [{given: x, when: y, then: z}]}
`

/** Fake agent. `over` can change any answer: over(stage, stepId, call) returns a value or undefined. */
function fake(hash, over = {}) {
  const pre = { planHash: hash, repoRoot: '/repo', defaultBranch: 'main', statuses: {}, ghStack: true, draftPrs: true, checks: ['make test'], checksSource: 'AGENTS.md', ...(over.pre || {}) }
  return (call) => {
    const label = call.label
    if (label === 'preflight') return pre
    let id, stage
    const mm = label.match(/^(\S+?)(?: \(retry\))?: (.+)$/)
    if (mm) { id = mm[1]; stage = mm[2] }
    if (over.hook) {
      const v = over.hook(stage, id, call)
      if (v !== undefined) return v
    }
    if (/^wave \d+/.test(label)) return { ok: true }
    switch (stage) {
      case 'build': return { ok: true, branch: `plan/demo/${id}`, worktree: `/wt/${id}`, summary: `built ${id}` }
      case 'checks': return { passed: true, unverified: false, ran: ['make test'], report: 'all green' }
      case 'review': return { allPass: true, verdicts: [{ index: 1, pass: true, reason: 'ok' }] }
      case 'publish': return { ok: true, pr: `https://pr/${id}`, mode: 'stack' }
      case 'CI': return { state: 'green', summary: '' }
      default: return { ok: true }
    }
  }
}

const stages = (calls, stage) => calls.filter((c) => c.label.endsWith(`: ${stage}`)).map((c) => c.label.split(':')[0].replace(' (retry)', ''))

test('runs steps in dependency order and builds a report', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash))
  const builds = stages(run.calls, 'build')
  assert.deepEqual(builds[0], 'a')
  assert.deepEqual(new Set(builds.slice(1, 3)), new Set(['b', 'c']))
  assert.equal(builds[3], 'd')
  assert.deepEqual(run.result.steps.map((s) => s.outcome), ['done', 'done', 'done', 'done'])
  assert.equal(run.result.stacksUsed, true)
  assert.match(run.result.text, /PR d: https:\/\/pr\/d/)
})

test('never runs more than 3 steps at once', async () => {
  const yaml = 'plan: {title: Wide}\nsteps:\n' + [1, 2, 3, 4, 5, 6, 7].map((n) => `  - {id: s${n}, title: S${n}, acceptance_criteria: [{given: x, when: y, then: z}]}`).join('\n')
  const { out, hash } = generate(yaml, 'wide')
  const run = await runWorkflow(out, fake(hash))
  assert.equal(run.result.steps.filter((s) => s.outcome === 'done').length, 7)
  assert.ok(run.maxActive <= 3, `saw ${run.maxActive} at once`)
})

test('a plan edit after generation stops the run', async () => {
  const { out } = generate(diamond)
  await assert.rejects(runWorkflow(out, fake('deadbeef0000')), /plan changed, regenerate/)
})

test('a changed status alone does not change the hash', () => {
  const a = generate(diamond)
  const b = generate(diamond.replace('{id: a, title: A,', '{id: a, status: done, title: A,'))
  assert.equal(a.hash, b.hash)
})

test('a failing step is retried once with the problem passed along', async () => {
  const { out, hash } = generate(diamond)
  let judged = 0
  const run = await runWorkflow(out, fake(hash, {
    hook(stage, id, call) {
      if (stage === 'review' && id === 'b' && judged++ === 0)
        return { allPass: false, verdicts: [{ index: 1, pass: false, reason: 'cache not used' }] }
    },
  }))
  assert.equal(run.result.steps.find((s) => s.id === 'b').outcome, 'done')
  const retry = run.calls.find((c) => c.label === 'b (retry): build')
  assert.ok(retry, 'retry build happened')
  assert.match(retry.prompt, /cache not used/)
  assert.match(retry.prompt, /existing worktree \/wt\/b/)
})

test('after one retry a step is failed and everything behind it is blocked', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash, {
    hook(stage, id) {
      if (stage === 'checks' && id === 'b') return { passed: false, unverified: false, report: 'lint broke' }
    },
  }))
  const outcome = Object.fromEntries(run.result.steps.map((s) => [s.id, s.outcome]))
  assert.deepEqual(outcome, { a: 'done', b: 'failed', c: 'done', d: 'blocked' })
  assert.equal(stages(run.calls, 'build').filter((x) => x === 'b').length, 2) // first try + one retry
  assert.ok(!stages(run.calls, 'build').includes('d'))
  assert.match(run.result.text, /Failed: b \(checks failed: lint broke\)/)
  assert.match(run.result.text, /Blocked: d/)
})

test('a step with two parents is a plain PR on the parent that finished last', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash, {
    hook(stage, id) {
      if (stage === 'publish' && id === 'd') return { ok: true, pr: 'https://pr/d', mode: 'plain' }
    },
  }))
  const pub = run.calls.find((c) => c.label === 'd: publish')
  assert.match(pub.prompt, /plain pull request with base branch plan\/demo\/c/)
  assert.match(pub.prompt, /b, c/)
  const build = run.calls.find((c) => c.label === 'd: build')
  assert.match(build.prompt, /git worktree add -b plan\/demo\/d \S+ plan\/demo\/c/)
  assert.match(build.prompt, /merge these finished parent branches[^\n]*plan\/demo\/b/)
})

test('a second child of the same parent gets a plain PR, not a stack', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash))
  const modes = ['b', 'c'].map((id) => /Open a plain pull request/.test(run.calls.find((c) => c.label === `${id}: publish`).prompt))
  assert.deepEqual(modes.sort(), [false, true])
})

test('without gh stack every PR is plain', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash, { pre: { ghStack: false } }))
  for (const c of run.calls.filter((c) => c.label.endsWith(': publish'))) assert.match(c.prompt, /Open a plain pull request/)
})

test('steps already done are skipped; base= changes the start point', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash, { pre: { statuses: { a: 'done' } } }), 'base=develop')
  assert.ok(!stages(run.calls, 'build').includes('a'))
  assert.equal(run.result.steps[0].outcome, 'already_done')
  const rootBuild = run.calls.find((c) => c.label === 'b: build')
  assert.ok(rootBuild)
  assert.match(rootBuild.prompt, /origin\/develop/)
})

test('base= is used for steps with no parent', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash), { base: 'develop' })
  assert.match(run.calls.find((c) => c.label === 'a: build').prompt, /origin\/develop/)
})

test('dryRun asks only the preflight agent and lists waves', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash), 'dryRun')
  assert.equal(run.calls.length, 1)
  assert.deepEqual(run.result.waves, [['a'], ['b', 'c'], ['d']])
  assert.match(run.result.text, /Wave 1: a \[create a\.txt\]/)
})

test('status is written only through plan_board status, never as a hand edit', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash))
  const status = run.calls.filter((c) => /^wave \d+: status$/.test(c.label))
  assert.ok(status.length >= 6)
  for (const c of status) assert.match(c.prompt, /plan_board\.py status \S+ \w+ (in_progress|done|not_started)/)
  assert.ok(status.every((c) => /do not commit it/.test(c.prompt)))
})

test('CI: up to 3 fix attempts, then a comment and the PR stays a draft', async () => {
  const { out, hash } = generate('plan: {title: One}\nsteps:\n  - {id: a, title: A, acceptance_criteria: [{given: x, when: y, then: z}]}', 'one')
  const run = await runWorkflow(out, fake(hash, {
    hook(stage) { if (stage === 'CI') return { state: 'red', summary: 'unit tests fail' } },
  }))
  assert.equal(run.calls.filter((c) => /: CI fix \d$/.test(c.label)).length, 3)
  assert.equal(run.calls.filter((c) => c.label === 'a: CI comment').length, 1)
  assert.equal(run.result.steps[0].ci, 'red')
  assert.match(run.result.text, /CI still red: a/)
})

test('CI that turns green after one fix stops there', async () => {
  const { out, hash } = generate('plan: {title: One}\nsteps:\n  - {id: a, title: A}', 'one')
  let seen = 0
  const run = await runWorkflow(out, fake(hash, {
    hook(stage) { if (stage === 'CI') return seen++ === 0 ? { state: 'red', summary: 'x' } : { state: 'green', summary: '' } },
  }))
  assert.equal(run.calls.filter((c) => /: CI fix/.test(c.label)).length, 1)
  assert.equal(run.result.steps[0].ci, 'green')
})

test('no checks found: the step goes ahead and the PR is marked unverified', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash, {
    pre: { checks: [] },
    hook(stage) { if (stage === 'checks') return { passed: true, unverified: true, ran: [], report: 'none found' } },
  }))
  assert.ok(run.result.steps.every((s) => s.unverified))
  assert.match(run.calls.find((c) => c.label === 'a: publish').prompt, /Unverified/)
  assert.match(run.result.text, /Unverified \(no checks found\): a, b, c, d/)
})

test('agent that throws in a build is retried and then fails the step, not the run', async () => {
  const { out, hash } = generate(diamond)
  const run = await runWorkflow(out, fake(hash, {
    hook(stage, id) { if (stage === 'build' && id === 'c') throw new Error('boom') },
  }))
  assert.equal(run.result.steps.find((s) => s.id === 'c').outcome, 'failed')
  assert.equal(run.result.steps.find((s) => s.id === 'b').outcome, 'done')
})
