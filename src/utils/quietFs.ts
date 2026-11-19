/**
 * 「安静地删掉」——一条文件系统纪律的落点。
 *
 * ## 为什么不能直接用 `rmSync` / `rmdirSync(recursive)`
 *
 * 2026-11-19 在本机（Windows + 沙箱）实测：
 *
 * | 调用 | 结果 |
 * | --- | --- |
 * | `rmSync(file, { force: true })` | 不抛错，**文件还在原处** |
 * | `rmSync(dir, { recursive: true, force: true })` | 不抛错，**目录还在原处** |
 * | `rmdirSync(dir, { recursive: true })` | 不抛错，**目录还在原处** |
 * | `unlinkSync(file)` | 真的删掉 |
 * | `rmdirSync(空目录)` | 真的删掉 |
 *
 * 后果不是"多留个垃圾文件"那么轻：删除失败会让**上面的语义**全错。真事两件——
 * `stopDaemon()` 报"已停止"却留着 pid 文件；记忆文件原子写失败后退化路径留 `.tmp`，
 * 下一条用例读到的是旧内容。两处都表现为"测试红了，但红的原因和被测逻辑无关"。
 *
 * ## 所以
 *
 * 生产代码里要删东西，一律走这里：单文件用 `unlinkSync`，目录按"先清空、再非递归 rmdir"
 * 手工递归。`test/no_rm_sync_test.ts` 会拦住 `src/` 里新写的 `rmSync` / `rmdirSync`。
 *
 * 删不掉不抛错：这些都是"收尾"，收不掉不该盖住调用方真正要处理的问题。确实需要知道
 * 删没删掉的场合，自己 `existsSync` 复查。
 */
import { existsSync, readdirSync, rmdirSync, statSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

/** 删一个文件。不存在也算成功。 */
export function removeFileQuietly(path: string): void {
  try { if (existsSync(path)) unlinkSync(path) } catch { /* 收不掉不影响正确性 */ }
}

/** 删一棵目录树。不存在也算成功。 */
export function removeDirQuietly(path: string): void {
  try {
    if (!existsSync(path)) return
    if (!statSync(path).isDirectory()) { unlinkSync(path); return }
    for (const name of readdirSync(path)) removeDirQuietly(join(path, name))
    rmdirSync(path)
  } catch { /* 见文件头：收尾失败不向上抛 */ }
}
