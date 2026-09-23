/**
 * 启动决策：用哪个二进制、argv 里有什么。
 *
 * **这一层是"窗口进不了 CI"的补偿**：窗口本身没有显示器，跑不了；但"用哪个二进制、传什么参数"
 * 全是纯函数，可以钉住。其中最重要的一条是**凭据不许进 argv**——Windows 上同机任何进程都能从
 * `wmic process get commandline` 读到命令行，把 token 放进去就是一次看不出破绽的泄漏。
 *
 * 检测函数一律**注入 `existsFn`**：CI 上 `npm ci` 会真的装 devDependencies，
 * `node_modules/electron/dist/electron.exe` 可能真存在——碰真实文件系统就等于测不了"找不到"。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { join } from 'node:path'

const { resolveElectronBinary, resolveBrowserBinary, electronLaunchArgs,
        browserLaunchArgs, browserProfileDir } = await import('../src/panel/launch.js')

const none = () => false
const only = (target: string) => (p: string) => p === target

// ---------------------------------------------------------------- Electron

test('找 Electron：包内优先，找不到全局再找一层', () => {
  const packageRoot = join('C:', 'proj')
  const localAppData = join('C:', 'Users', 'u', 'AppData', 'Local')
  const r = resolveElectronBinary({ packageRoot, localAppData, exists: none })
  assert.equal(r.found, null)
  assert.ok(r.candidates.length >= 2, '至少有包内与全局两个候选')
  assert.match(r.candidates[0], /node_modules[\\/]electron[\\/]dist[\\/]electron\.exe$/)
  assert.ok(r.candidates[0].startsWith(packageRoot), '第一个候选是包内')
})

test('包内没有、全局有 → 用全局那个', () => {
  // 期望值必须**用 join 派生**，不能手写分隔符：第一版写的是
  // `'C:/Users/…/electron.exe'.replace(/\//g, '\\')`，在 Linux 上永远匹配不上（CI 抓到了）。
  const localAppData = join('C:', 'Users', 'u', 'AppData', 'Local')
  const globalElectron = join(localAppData, 'npm', 'node_modules', 'electron', 'dist', 'electron.exe')
  const r = resolveElectronBinary({ packageRoot: join('C:', 'proj'), localAppData, exists: only(globalElectron) })
  assert.equal(r.found, globalElectron, `实际拿到 ${r.found}`)
})

test('Electron 的 argv **只有一个参数**：应用目录', () => {
  const args = electronLaunchArgs('C:/proj/resources/panel')
  assert.deepEqual(args, ['C:/proj/resources/panel'])
})

// ---------------------------------------------------------------- 浏览器降级

test('浏览器候选：Edge 排在 Chrome 前面', () => {
  const r = resolveBrowserBinary({ programFiles: 'C:/PF', programFilesX86: 'C:/PF86', exists: none })
  const edgeIdx = r.candidates.findIndex((p) => /msedge\.exe$/.test(p))
  const chromeIdx = r.candidates.findIndex((p) => /chrome\.exe$/.test(p))
  assert.ok(edgeIdx >= 0 && chromeIdx >= 0)
  assert.ok(edgeIdx < chromeIdx, 'Windows 上优先用自带的 Edge')
})

test('浏览器的 argv：--app、独立 profile、跳过首次运行', () => {
  const args = browserLaunchArgs({ url: 'http://127.0.0.1:8766/panel?c=abc', profileDir: 'C:/home/.weflow-cli/panel-browser-profile' })
  assert.deepEqual(args, [
    '--app=http://127.0.0.1:8766/panel?c=abc',
    '--user-data-dir=C:/home/.weflow-cli/panel-browser-profile',
    '--no-first-run',
    '--no-default-browser-check',
  ])
})

test('浏览器 profile 必须在 .weflow-cli 里，不能蹭用户日常那个', () => {
  // 不带 --user-data-dir 的话，--app 会挤进用户日常那个浏览器实例（共享会话），
  // 甚至可能被并进已有窗口。所以这个目录必须是独立的——它也要进隐私文档的敏感路径清单。
  const dir = browserProfileDir('C:/home/.weflow-cli')
  assert.match(dir.split('\\').join('/'), /\/\.weflow-cli\/panel-browser-profile$/)
})

// ---------------------------------------------------------------- 凭据

test('两个启动器的 argv 里都不含任何凭据形状的东西', () => {
  // 这是本文件里最重要的一条。token 是 32 字节 base64url（43 个字符），
  // 一次性口令也长这样——所以一起挡。
  const tokenish = /[A-Za-z0-9_-]{30,}/
  for (const args of [
    electronLaunchArgs('C:/proj/resources/panel'),
    browserLaunchArgs({ url: 'http://127.0.0.1:8766/panel?c=SHORTLIVED', profileDir: 'C:/h/.weflow-cli/panel-browser-profile' }),
  ]) {
    const joined = args.join(' ')
    assert.doesNotMatch(joined, /bearer|token|authorization/i, `argv 里出现了凭据相关的字样：${joined}`)
    assert.doesNotMatch(joined, tokenish, `argv 里出现了凭据形状的长串：${joined}`)
  }
})

test('Electron 那条路不接受 URL 参数 —— 它自己去读端点文件，所以命令行永远干净', () => {
  // 函数签名就是这条纪律：`electronLaunchArgs` 只有目录一个入参，没有地方塞 token 或端口。
  assert.equal(electronLaunchArgs.length, 1)
})
