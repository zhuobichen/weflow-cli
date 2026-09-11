import { execFile } from 'child_process'
import { readFileSync } from 'fs'
import { promisify } from 'util'
import { join } from 'path'
import { keyService } from '../src/core/keyService.js'
import { getPythonCommand } from '../src/utils/python.js'
import { createPythonProcessEnv } from '../src/utils/pythonProcessEnv.js'

const execFileAsync = promisify(execFile)
const dbPath = process.argv[2]

if (!dbPath) {
  console.error('Usage: npx tsx scripts/capture_favorite_schema.ts <favorite.db path>')
  process.exit(1)
}

const capture = await keyService.captureDatabaseKey(90_000, dbPath, message => {
  console.log(message)
})

if (!capture.success || !capture.key) {
  console.error(capture.error || '收藏库密钥捕获失败')
  process.exit(1)
}

const scriptPath = join(process.cwd(), 'scripts', 'nt_decrypt.py')
const salt = readFileSync(dbPath).subarray(0, 16).toString('hex')
// Resolve the interpreter the same way the CLI does; `py -3` is absent on
// many Windows installs and disagrees with the venv the dependencies live in.
const { stdout } = await execFileAsync(getPythonCommand(), [
  scriptPath, 'schema', '--db', dbPath, '--key', capture.key, '--salt', salt,
], {
  timeout: 15_000,
  maxBuffer: 1024 * 1024,
  env: createPythonProcessEnv(),
  encoding: 'utf-8',
})

const line = stdout.split('\n').filter(value => value.trim().startsWith('{')).at(-1)
const schema = JSON.parse(line || '{}')
console.log(JSON.stringify({ success: Boolean(schema.success), tables: schema.tables || [] }))
