import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

type Todo = {
  id: string
  task: string
  status: 'pending' | 'done'
  urgency: string
  deadline: string
}

test('todo mutations require preview and explicit confirmation in JSON mode', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-todos-'))
  const stateDir = join(home, '.weflow-cli')
  const stateFile = join(stateDir, 'todos.json')
  const initial: Todo[] = [{
    id: 'todo-test-001',
    task: 'synthetic task',
    status: 'pending',
    urgency: '低',
    deadline: 'not specified',
  }]
  const run = (...args: string[]) => spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'),
    'todos', ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: {
      ...process.env,
      HOME: home,
      USERPROFILE: home,
      PYTHONIOENCODING: 'utf-8',
    },
  })
  const readTodos = (): Todo[] => JSON.parse(readFileSync(stateFile, 'utf8'))

  try {
    mkdirSync(stateDir, { recursive: true })
    writeFileSync(stateFile, JSON.stringify(initial, null, 2), 'utf8')

    const reminder = run('remind', '--json')
    assert.equal(reminder.status, 0, reminder.stderr || reminder.stdout)
    assert.equal(JSON.parse(reminder.stdout).total_pending, 1)
    assert.equal(JSON.parse(reminder.stdout).low[0].task, initial[0].task)

    const preview = run('done', 'todo-test', '--dry-run', '--json')
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.deepEqual(JSON.parse(preview.stdout), {
      success: true,
      dryRun: true,
      action: 'done',
      todo: initial[0],
    })
    assert.deepEqual(readTodos(), initial)

    const unconfirmed = run('done', 'todo-test', '--json')
    assert.equal(unconfirmed.status, 1)
    assert.equal(JSON.parse(unconfirmed.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.deepEqual(readTodos(), initial)

    const completed = run('done', 'todo-test', '--yes', '--json')
    assert.equal(completed.status, 0, completed.stderr || completed.stdout)
    assert.equal(JSON.parse(completed.stdout).todo.status, 'done')
    assert.equal(readTodos()[0].status, 'done')

    const removed = run('rm', 'todo-test', '--yes', '--json')
    assert.equal(removed.status, 0, removed.stderr || removed.stdout)
    assert.equal(JSON.parse(removed.stdout).action, 'remove')
    assert.deepEqual(readTodos(), [])
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
