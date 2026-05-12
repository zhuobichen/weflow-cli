#!/usr/bin/env node

/**
 * WeFlow CLI wrapper - uses Electron runtime to satisfy wcdb_api.dll security check
 *
 * Uses ELECTRON_RUN_AS_NODE=1 to run in Node.js mode within an Electron process.
 * The -e flag is used instead of passing the script file directly, because commander.js
 * in Electron's Node.js mode doesn't correctly skip process.argv[1] (the script path),
 * treating it as an unknown command instead.
 */

const { execFileSync } = require('child_process');
const { join, resolve } = require('path');
const { existsSync } = require('fs');

const scriptDir = __dirname;
const projectRoot = resolve(scriptDir, '..');

function getElectronPath() {
  // Use the actual electron binary for execFileSync
  const electronDist = join(projectRoot, 'node_modules', 'electron', 'dist', 'electron.exe');
  if (existsSync(electronDist)) return electronDist;

  if (process.platform === 'darwin') {
    return join(projectRoot, 'node_modules', 'electron', 'dist', 'Electron.app', 'Contents', 'MacOS', 'Electron');
  }

  return join(projectRoot, 'node_modules', 'electron', 'dist', 'electron');
}

const cliEntry = join(projectRoot, 'dist', 'bin', 'weflow-cli.js');

if (!existsSync(cliEntry)) {
  console.error('Error: CLI entry point not found. Run "npm run build" first.');
  process.exit(1);
}

const args = process.argv.slice(2);
const electronPath = getElectronPath();
const env = { ...process.env, ELECTRON_RUN_AS_NODE: '1' };

// Use file:// URL for ESM import compatibility
const fileUrl = 'file:///' + cliEntry.replace(/\\/g, '/');
const script = `import('${fileUrl}')`;
const electronArgs = ['-e', script, '--', ...args];

try {
  execFileSync(electronPath, electronArgs, {
    stdio: 'inherit',
    env,
    cwd: process.cwd()
  });
} catch (err) {
  if (err.status !== undefined) {
    process.exit(err.status);
  }
  console.error('Failed to run weflow-cli:', err.message);
  process.exit(1);
}
