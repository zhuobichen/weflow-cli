/**
 * **"这台机器上的微信是哪个版本"** —— 判据来自一份实测的启动器逆向分析（报告在仓库外）。
 *
 * 它纠正了两件事，两条都在这里钉住：
 *
 * 1. **别读 exe 里的版本字面量**。它硬编码着、而且恰好与本机版本目录同名，看着像"读它就够了"，
 *    实际只是**兜底**（主路径读 `update\update.data`）。⇒ 版本只能从**活进程命令行**的
 *    `--user-lib-dir` 取（那个参数在 exe 的字符串表里 0 命中，二进制里搜不到）。
 * 2. **"磁盘上有版本目录" ≠ "它能用"**。本机 `4.1.15.11\` 是升级残留、只剩一个文件，
 *    指向它的加载会失败 ⇒ 判据是**有没有 `Weixin.dll`**。
 *
 * 另外两条是"不猜"：多个进程指向不同目录时**不给**单一版本号；客户端没在跑时报**未知**
 * （不是"没问题"）—— 这一条与仓库里那条"没找到必须报 UNKNOWN，别报 CLEAR"是同一条纪律。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  classifyVersionDirs, clientVersionToken, crashSummary, detectWechatClient,
  parseProcessRows, parseViewFiltered, PS_LIST, PS_VIEW_FILTERED, summarize,
  updateDataCaveats, userLibDir, versionDirsUnder, wechatFilesPath, xwechatRoot,
} from '../src/services/wechatClient.js'

test('PowerShell 输出必须走 base64（中文路径会被控制台代码页弄坏）', () => {
  // PowerShell 5.1 往 stdout 写的是控制台代码页（中文 Windows 是 GBK），Node 按 UTF-8 解，
  // 于是 `D:\微信\xwechat_files` 变成 `D:\΢��\xwechat_files`，"与配置同一份"被判成"否"，
  // 报告就会建议用户去"对齐账号"——2026-10-19 在真机上就是这么误报的。
  assert.match(PS_LIST, /ToBase64String/, 'PS_LIST 必须 base64 输出')
  assert.match(PS_LIST, /Encoding\]::Unicode/, '要用 UTF-16LE，与 Node 的 utf16le 对称')
  // 解码这一步是纯计算，直接验往返
  const sample = 'D:\\微信\\xwechat_files'
  const b64 = Buffer.from(sample, 'utf16le').toString('base64')
  assert.equal(Buffer.from(b64, 'base64').toString('utf16le'), sample)
})

/** 进程画像的默认值，省得每处都写那两个可选字段。 */
const proc = (pid: number, loadedDir: string | null, dataDir: string | null = null,
              versionToken: string | null = null, dllPath: string | null = null) =>
  ({ pid, exe: 'D:\\WeiXin\\Weixin.exe', loadedDir, dataDir, versionToken, dllPath })

test('命令行里取 --user-lib-dir：带引号、不带引号、带空格都要认', () => {
  assert.equal(userLibDir('"D:\\WeiXin\\Weixin.exe" --user-lib-dir="D:\\WeiXin\\4.1.15.13" --no-sandbox'),
    'D:\\WeiXin\\4.1.15.13')
  assert.equal(userLibDir('Weixin.exe --user-lib-dir=D:\\WeiXin\\4.1.15.13'), 'D:\\WeiXin\\4.1.15.13')
  assert.equal(userLibDir("Weixin.exe --user-lib-dir='C:\\a b\\4.1.2'"), 'C:\\a b\\4.1.2')
  assert.equal(userLibDir('Weixin.exe --no-sandbox --type=wxocr'), null)
  assert.equal(userLibDir(''), null)
})

test('进程表：ConvertTo-Json 给对象（单个进程）与数组都要收，坏 JSON 不炸', () => {
  const one = JSON.stringify({ ProcessId: 36248, ExecutablePath: 'D:\\WeiXin\\Weixin.exe',
                               CommandLine: 'Weixin.exe --scene=desktop' })
  const rows = parseProcessRows(one)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].pid, 36248)
  assert.equal(rows[0].loadedDir, null, '主进程不带 --user-lib-dir 是实测到的形状')

  const many = JSON.stringify([
    { ProcessId: 1, ExecutablePath: 'D:\\WeiXin\\Weixin.exe', CommandLine: 'x --user-lib-dir="D:\\WeiXin\\4.1.15.13"' },
    { ProcessId: 2, ExecutablePath: 'D:\\WeiXin\\Weixin.exe', CommandLine: 'x --type=xplayer' },
  ])
  const pair = parseProcessRows(many)
  assert.deepEqual(pair.map(p => p.loadedDir), ['D:\\WeiXin\\4.1.15.13', null])
  assert.deepEqual(parseProcessRows('not json'), [])
  assert.deepEqual(parseProcessRows(''), [])
})

test('进程表：带回实际映射的 Weixin.dll，读不到时是 null（不是空串）', () => {
  const rows = parseProcessRows(JSON.stringify([
    { ProcessId: 3, ExecutablePath: 'D:\\WeiXin\\Weixin.exe', CommandLine: '"D:\\WeiXin\\Weixin.exe"',
      WeixinDll: 'D:\\WeiXin\\4.1.15.53\\Weixin.dll' },
    { ProcessId: 4, ExecutablePath: 'D:\\WeiXin\\Weixin.exe', CommandLine: 'x', WeixinDll: null },
  ]))
  assert.equal(rows[0].dllPath, 'D:\\WeiXin\\4.1.15.53\\Weixin.dll')
  assert.equal(rows[1].dllPath, null)
})

test('能用的版本目录 = 有 Weixin.dll 的；残留目录（只剩个别文件）要报出来', () => {
  const got = classifyVersionDirs([
    { name: '4.1.15.13', files: ['Weixin.dll', 'Weixin.exe', 'plugin_info.ini'] },
    // 实测形状：升级残留，只剩 crashpad_handler.exe 一个文件 —— 指向它的加载会失败
    { name: '4.1.15.11', files: ['crashpad_handler.exe'] },
    { name: 'locales', files: ['zh-CN.pak'] },        // 名字不是版本号 ⇒ 不参与
  ])
  assert.deepEqual(got.usable, ['4.1.15.13'])
  assert.deepEqual(got.unusable, [{ dir: '4.1.15.11', files: 1 }])
})

test('磁盘上讲一遍：安装根下哪些版本目录能用、哪些是残留', () => {
  const root = mkdtempSync(join(tmpdir(), 'weixin-install-'))
  try {
    mkdirSync(join(root, '4.1.15.13'))
    writeFileSync(join(root, '4.1.15.13', 'Weixin.dll'), 'x')
    mkdirSync(join(root, '4.1.15.11'))
    writeFileSync(join(root, '4.1.15.11', 'crashpad_handler.exe'), 'x')
    mkdirSync(join(root, 'locales'))
    const got = versionDirsUnder(root)
    assert.deepEqual(got.usable, ['4.1.15.13'])
    assert.deepEqual(got.unusable, [{ dir: '4.1.15.11', files: 1 }])
    // 读不出来（目录不存在）⇒ 空，不是"没有问题"
    assert.deepEqual(versionDirsUnder(join(root, 'nope')), { usable: [], unusable: [] })
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('多个进程指向不同目录：列出来，但不给一个"版本号"', () => {
  const facts = summarize([
    proc(1, 'D:\\WeiXin\\4.1.15.13'),
    proc(2, 'C:\\微信\\Weixin\\4.1.13.12'),
  ], [])
  assert.equal(facts.version, null)
  assert.deepEqual(facts.loadedDirs, ['D:\\WeiXin\\4.1.15.13', 'C:\\微信\\Weixin\\4.1.13.12'])
  assert.match(facts.note, /不挑一个报/)
})

test('进程视图被隔离时不许说"没在运行"（2026-10-28 实测踩过）', () => {
  // 现场：`check` 报"主程序 Weixin.exe 没在运行"，而同一时刻 `general.db` / `contact.db`
  // 正在被写 —— 微信明明在跑。原因是本进程的进程视图被沙箱隔离：可见进程名只有 16 个，
  // `System`/`lsass`/`svchost`/`csrss` 一个都看不到。
  // 判据：这时能确定的只有"看不见"，所以话只能说到"无法判断"。
  const appex = [{
    pid: 1, exe: 'C:\\...\\WeChatAppEx.exe', loadedDir: null, dataDir: null,
    versionToken: null, dllPath: null,
  }]

  const onlyHelpers = summarize(appex as never, [], true)
  assert.equal(onlyHelpers.version, null)
  assert.match(onlyHelpers.note, /视图被隔离/)
  assert.match(onlyHelpers.note, /无法判断/)
  assert.match(onlyHelpers.note, /不代表客户端没在运行/)
  assert.doesNotMatch(onlyHelpers.note, /没在运行 ⇒/, '隔离视图下不能断言客户端没在运行')

  const nothing = summarize([], [], true)
  assert.match(nothing.note, /视图被隔离/)
  assert.match(nothing.note, /无法判断/)
  assert.doesNotMatch(nothing.note, /没在运行 ⇒/)

  // 没被隔离时，原来的说法不变（这条是上面两条的对照，不能被顺手放宽）
  assert.match(summarize([], [], false).note, /没在运行/)
  assert.match(summarize(appex as never, [], false).note, /主程序 Weixin.exe 没在运行/)
})

test('parseViewFiltered 只认探针那一行', () => {
  assert.equal(parseViewFiltered('filtered'), true)
  assert.equal(parseViewFiltered('  filtered\r\n'), true)
  assert.equal(parseViewFiltered(''), false)
  assert.equal(parseViewFiltered('1'), false)
  // 探针没输出（比如 PowerShell 不可用）不能当成"视图被隔离"
  assert.equal(parseViewFiltered('随便什么'), false)
  // **必须 base64 输出**：`defaultRunner` 对非 JSON 输出一律 base64 解码，
  // 裸字符串会被解成乱码 → 探针永远返回"没被隔离"，而单元测试照样绿
  // （2026-10-28 实测踩过：dist 里代码是新的，check 还是旧话）。
  assert.match(PS_VIEW_FILTERED, /ToBase64String/, 'PS_VIEW_FILTERED 必须 base64 输出')
})

test('没在跑 / 都没带参数：版本是 null 且说清为什么（不是"没问题"）', () => {
  const idle = summarize([], [])
  assert.equal(idle.running, false)
  assert.equal(idle.version, null)
  assert.match(idle.note, /没在运行/)

  const noFlag = summarize([proc(1, null)], [])
  assert.equal(noFlag.running, true)
  assert.equal(noFlag.version, null)
  assert.match(noFlag.note, /都没有 --user-lib-dir/)
})

test('命令行没带 --user-lib-dir 时，版本取自实际加载的 Weixin.dll（4.1.15.53 起主进程这样）', () => {
  // 2026-10-18 本机实测：升到 4.1.15.53 后主进程命令行只有 `"D:\WeiXin\Weixin.exe"`，
  // 一个开关都没有 —— 但模块表里的 Weixin.dll 明确在 4.1.15.53\ 下。
  const facts = summarize([proc(1, null, null, null, 'D:\\WeiXin\\4.1.15.53\\Weixin.dll')], [])
  assert.equal(facts.version, '4.1.15.53')
  assert.deepEqual(facts.loadedDirs, ['D:\\WeiXin\\4.1.15.53'])
  assert.match(facts.note, /Weixin\.dll/, '说清版本是从模块表来的')
})

test('命令行优先：带了就不看模块表（两种观测一致时不能被算成两个目录）', () => {
  const facts = summarize([
    proc(1, 'D:\\WeiXin\\4.1.15.53', null, null, 'D:\\WeiXin\\4.1.15.53\\Weixin.dll'),
  ], [])
  assert.equal(facts.version, '4.1.15.53')
  assert.deepEqual(facts.loadedDirs, ['D:\\WeiXin\\4.1.15.53'])
  assert.equal(facts.note, '', '命令行给了就不必解释来源')
})

test('模块表里出现两个目录：仍然不挑一个报版本', () => {
  const facts = summarize([
    proc(1, null, null, null, 'D:\\WeiXin\\4.1.15.53\\Weixin.dll'),
    proc(2, null, null, null, 'D:\\WeiXin\\4.1.15.13\\Weixin.dll'),
  ], [])
  assert.equal(facts.version, null)
  assert.equal(facts.loadedDirs.length, 2)
  assert.match(facts.note, /不挑一个报/)
})

test('正常一台：版本就是加载目录的名字，且重复的目录只算一次', () => {
  const facts = summarize([
    proc(1, 'D:\\WeiXin\\4.1.15.13'),
    proc(2, 'D:\\WeiXin\\4.1.15.13\\'),
  ], [])
  assert.equal(facts.version, '4.1.15.13')
  assert.equal(facts.note, '')
})

test('detectWechatClient：非 Windows 直接说平台不支持，不是"没问题"', async () => {
  const real = process.platform
  Object.defineProperty(process, 'platform', { value: 'linux', configurable: true })
  try {
    const facts = await detectWechatClient(async () => { throw new Error('不该被调用') })
    // Linux 现在走 detectLinuxClient()：扫 /proc 判运行、读 ~/.xwechat/uh/uh.dat 取版本。
    // 所以"未知"的措辞不再是"只做了 Windows"——那句话在 Linux 能探测之后就成了错的。
    assert.equal(facts.version, null)
    assert.doesNotMatch(facts.note, /只做了 Windows/, 'Linux 已经能探测，不该再说只支持 Windows')
    assert.ok(facts.note.length > 0, '必须给出"为什么未知"')
  } finally {
    Object.defineProperty(process, 'platform', { value: real, configurable: true })
  }
})

test('detectWechatClient：PowerShell 报错时回"未知"，不抛也不假装没问题', async () => {
  // 这条测的是 **Windows 那条路**上 PowerShell 挂了时的反应，所以要把平台钉成 win32 ——
  // 否则在 Linux 上（CI）`detectWechatClient` 会先走"平台不支持"那一支，连 runner 都不调，
  // 于是这里断言 `/没在运行/` 必报红（2026-10-11 CI 实测：这是那批里最后一条）。手法同上面
  // 那条"非 Windows 直接说平台不支持"：测的是**代码的哪一条分支**，不是宿主的平台。
  const real = process.platform
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true })
  try {
    const facts = await detectWechatClient(async () => { throw new Error('powershell 不在') })
    assert.equal(facts.version, null)
    assert.equal(facts.running, false)
    assert.match(facts.note, /没在运行/)
  } finally {
    Object.defineProperty(process, 'platform', { value: real, configurable: true })
  }
})

test('命令行里取 --wechat-files-path：客户端自己说数据目录在哪', () => {
  assert.equal(wechatFilesPath('WeChatAppEx.exe --wechat-files-path="D:\\微信\\xwechat_files"'),
    'D:\\微信\\xwechat_files')
  assert.equal(wechatFilesPath('x --wechat-files-path=D:\\a'), 'D:\\a')
  assert.equal(wechatFilesPath('x --type=renderer'), null)
  assert.equal(wechatFilesPath(''), null)
})

test('客户端自报的构建 token 要报出来，但**不拿它推版本号**', () => {
  // 实测：本机 AppEx 命令行上的 `--client_version=4065599245` 正是 `0xF2541F0D` —— 那份逆向报告
  // 从反编译 / 插件 manifest / UA 三处定的版本号，这里是**第四个独立来源**。
  assert.equal(clientVersionToken('WeChatAppEx.exe --client_version=4065599245'), '0xF2541F0D')
  // 第二组用机器算出来的值（手算十六进制我第一版就写错了一个字符 —— 这类期望值一律机器算）
  assert.equal(clientVersionToken('--client_version=1728058427'), '0x6700143B')
  assert.equal(clientVersionToken('--client_version=0'), null, '0 不是版本')
  assert.equal(clientVersionToken('x --type=renderer'), null)
  // 低 16 位已解出（0x1F0D→4.1.15.13、0x1F35→4.1.15.53），但高 16 位在所有样本里都是 F254、
  // 而它们都是 4.1.x ⇒ 主次版本分不开，代码不拿 token 推版本号。这条断言钉的是
  // "它就是个 token"，格式固定为 0x + 8 位大写十六进制。
  assert.match(clientVersionToken('--client_version=1')!, /^0x[0-9A-F]{8}$/)
})

test('崩溃记录：只数条数与最近时间，不读内容；目录不在时不许拿当前时间顶替', () => {
  const root = mkdtempSync(join(tmpdir(), 'crashinfo-'))
  try {
    mkdirSync(join(root, 'reports'))
    mkdirSync(join(root, 'records'))
    mkdirSync(join(root, 'metadata'))
    writeFileSync(join(root, 'reports', 'a.dmp'), 'x')
    writeFileSync(join(root, 'metadata', 'b'), 'x')
    const got = crashSummary(root)
    assert.equal(got.reports, 1)
    assert.equal(got.records, 1, 'records + metadata 一起数')
    assert.ok(got.newest, '拿到最近时间')

    const empty = crashSummary(join(root, 'nope'))
    assert.deepEqual({ reports: empty.reports, records: empty.records, newest: empty.newest },
      { reports: 0, records: 0, newest: null },
      'newest 必须是 null —— 拿当前时间顶替会造出一个假的"最近崩溃"')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('update.data 在场时，必须把"版本未必是它"这句话一起带出去', () => {
  assert.deepEqual(updateDataCaveats(false), [])
  const caveats = updateDataCaveats(true)
  assert.equal(caveats.length, 1)
  assert.match(caveats[0], /未必是实际加载的那个/)
})

test('xwechat 根从 APPDATA 推；没有 APPDATA 就返回 null（不猜）', () => {
  assert.equal(xwechatRoot('C:\\Users\\x\\AppData\\Roaming'),
    join('C:\\Users\\x\\AppData\\Roaming', 'Tencent', 'xwechat'))
  // 用**空串**表达"没有 APPDATA"：传 `undefined` 会命中默认参数（拿真的环境变量），
  // 那是这个函数想要的行为，但它测不出"没有 APPDATA"这一支。
  assert.equal(xwechatRoot(''), null)
})
