/**
 * 落盘失败**不许静默**。
 *
 * `save()` 里那个 `catch { /* 持久化失败不阻断对话 *\/ }` 曾经把每一次写失败都吞掉：不抛是
 * 对的（磁盘打嗝不该毁掉这次对话），但一声不吭是错的——用户说"记住"，写失败，这条记忆就是
 * 没了，而没有任何地方留下痕迹。实测撞到过：一次临时目录上的写失败让两条"记忆能跨重启"的
 * 测试失败，系统里却没有任何记录指向它。
 *
 * **这个文件必须单独存在**：要制造写失败，得在 `assistantMemory` 被 import *之前*把家目录
 * 布置成一个写不进去的样子——而模块级的路径是 import 那一刻算出来的，同一个进程里改不了。
 * 第一版我把这两条塞进了 `assistant-memory.test.ts`，写出来的用例根本做不到它名字里说的事
 * （那种测试永远不会为正确的原因失败）。
 *
 * 制造失败的方式见下面那段注释（把 `.tmp` 那个名字用目录占住，让写入那一步抛 EISDIR）——
 * 挑这个点是因为**审计文件本身仍然可写**，于是"失败留痕"这件事才有东西可断言。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-memory-savefail-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME
const DIR = join(HOME, '.weflow-cli')
mkdirSync(DIR, { recursive: true })
const MEMORY_PATH = join(DIR, 'assistant_memory.json')
const AUDIT_PATH = join(DIR, 'assistant_audit.log')

// 制造失败的方式：**正常写一份记忆文件**（于是 load 不会走留档路径），只把 `.tmp`
// 那个名字用一个目录占住——fail 发生在 `writeFileSync(tmp)` 那一步（EISDIR）。
//
// 第一版是拿目录占住 `assistant_memory.json` 本身，结果被 load 打败了：读一个目录会抛
// EISDIR，于是 `load()` 走**留档**路径把它改名挪走，等到 save 时那个名字已经空出来、写成功了。
// 夹具制造的那个"失败"根本不存在，而测试报的是"原因没留在实例上"。
mkdirSync(MEMORY_PATH + '.tmp')
writeFileSync(MEMORY_PATH, JSON.stringify({ version: 1, users: {} }), 'utf8')

const { AssistantMemory } = await import('../src/services/assistantMemory.js')

test('落盘失败：不抛异常，但原因留在实例上', () => {
  const memory = new AssistantMemory()
  memory.addFact('u-savefail', '这条事实本该落盘')

  assert.doesNotThrow(() => memory.save(), '落盘失败不该把这次对话打断')
  assert.ok(memory.lastSaveError, '失败原因必须留在实例上，否则外面无从知道')
  assert.match(memory.lastSaveError!, /E|denied|perm|dir/i, `看不出是什么失败：${memory.lastSaveError}`)
})

test('落盘失败会写进审计（只记错误码，不记消息）', () => {
  const memory = new AssistantMemory()
  memory.addFact('u-savefail', '再来一条')
  memory.save()

  const audit = existsSync(AUDIT_PATH) ? readFileSync(AUDIT_PATH, 'utf8') : ''
  assert.match(audit, /MEMORY_SAVE_FAILED/, '失败必须在审计里留痕——这正是原来缺的那一环')
  assert.match(audit, /code=/, '要带上错误码，否则仍然查不出因为什么')
})

test('内存态没有被这次失败破坏：事实还在，窗口还在', () => {
  const memory = new AssistantMemory()
  memory.addTurn('u-keep', 'user', '说一句话')
  memory.addFact('u-keep', '记住这件事')
  memory.save()      // 失败
  assert.equal(memory.workingWindow('u-keep')[0].content, '说一句话')
  assert.deepEqual(memory.facts('u-keep').map(f => f.content), ['记住这件事'])
})

test('下一次成功落盘会把失败记录清掉', () => {
  rmSync(MEMORY_PATH + '.tmp', { recursive: true, force: true })   // 把占住 .tmp 的目录挪开
  const memory = new AssistantMemory()
  memory.addFact('u-ok', '这次能存上')
  memory.save()

  assert.equal(memory.lastSaveError, null, '存成功了就不该再留着旧的失败')
  const saved = JSON.parse(readFileSync(MEMORY_PATH, 'utf8'))
  assert.equal(saved.version, 1)
  assert.ok(saved.users['u-ok'], '这一次确实落盘了')
})
