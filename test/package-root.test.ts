import test from 'node:test'
import assert from 'node:assert/strict'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { resolvePackageRoot } from '../src/utils/packageRoot.js'

test('package resources resolve identically from source and compiled module layouts', () => {
  const root = process.cwd()
  const sourceModule = pathToFileURL(join(root, 'src', 'services', 'assistantTools.ts')).href
  const compiledModule = pathToFileURL(join(root, 'dist', 'src', 'services', 'assistantTools.js')).href
  const compiledMcp = pathToFileURL(join(root, 'dist', 'mcp-server', 'index.js')).href

  assert.equal(resolvePackageRoot(sourceModule), root)
  assert.equal(resolvePackageRoot(compiledModule), root)
  assert.equal(resolvePackageRoot(compiledMcp), root)
})
