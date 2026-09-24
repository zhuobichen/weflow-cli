/**
 * 原子写：先写 `.tmp`，再 `rename` 覆盖目标。
 *
 * ## 为什么不能直接 `renameSync`
 *
 * **Windows 上 rename 覆盖一个正被别的句柄打开的文件会失败（EPERM）**——只读打开就够触发。
 * 实测：另一个句柄 `openSync(target, 'r')` 拿着时 `renameSync` 报 EPERM，句柄一关就成功。
 * 杀毒、搜索索引器、另一个进程的读者都会短暂造成这个状态。
 *
 * 后果是**静默丢写入**：调用方（`AssistantMemory.save()`、`configService.save()`）都是
 * catch 掉、只记一个原因，于是"保存成功"与"保存没发生"在界面上看不出区别。
 * 这不是理论风险——本仓库一次全量测试里，记忆文件那次保存之后就真的没写进去
 * （`save()` 之后文件里没有 `version`，而那条用例本身是绿的）。
 *
 * ## 所以
 *
 * 忙就重试几次（同步 sleep：调用方是同步的 `save()`，不值得为它把整条链改成异步），
 * 仍然忙就**直接覆盖写目标**——保住这份数据，比保住"原子"这个性质更重要。
 *
 * 落盘的字节与原子写一致（都是同一份字符串），所以读者不会看到半截内容；
 * 退化的那一次只失去"改名是瞬时"这一点。留给同机的读者一个真实边界：
 * 若那个句柄是以拒绝写入的方式打开的，直接写也会失败，这时照旧抛给调用方。
 */
import { writeFileSync, renameSync, rmSync } from 'node:fs'

/** 忙等。`Atomics.wait` 是标准库里的同步睡眠，不用引第三方包 */
function sleepSync(ms: number): void {
  try {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)
  } catch { /* 不支持就干脆不等，直接重试 */ }
}

/** 这些是"文件正忙"的形态，值得重试；别的错误（权限、路径不存在）立刻抛 */
const BUSY_CODES = new Set(['EPERM', 'EACCES', 'EBUSY'])

export function writeFileAtomic(target: string, data: string, options: { mode?: number } = {}): void {
  const tmp = `${target}.tmp`
  writeFileSync(tmp, data, { encoding: 'utf8', mode: options.mode })

  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      renameSync(tmp, target)
      return
    } catch (error: any) {
      if (!BUSY_CODES.has(error?.code)) {
        try { rmSync(tmp, { force: true }) } catch { /* 清理失败不掩盖原错误 */ }
        throw error
      }
      sleepSync(10 * (attempt + 1))
    }
  }

  // 还在忙：直接覆盖。数据不丢，只是不再"原子"
  writeFileSync(target, data, { encoding: 'utf8', mode: options.mode })
  try { rmSync(tmp, { force: true }) } catch { /* 见上 */ }
}
