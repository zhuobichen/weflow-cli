/**
 * 审计路径必须**可覆盖、且每次调用时算**。
 *
 * 这条来自一次真实失效（2026-11-20）：`AUDIT_FILE` 是模块加载时用 `homedir()` 定死的常量。
 * 于是评测把家目录指向临时目录之后，**审计仍写向真实用户的文件**——而那个文件在某些
 * 环境里不可写（本机沙箱 `EPERM`），写入失败还被 `catch {}` 吞掉。
 *
 * 连带的后果比"少写一行日志"严重得多：评测的观测正是从这份审计里读 `tools`，
 * 于是**所有 `mustCall` 断言都误判成"一个工具都没调"**，而助手其实调了。
 * 一个看着像"模型很笨"的结论，真正的原因是读错了文件。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, existsSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const { privacyGate } = await import('../src/services/assistantPrivacy.js')
const { readAuditSince, auditSize } = await import('../src/services/assistantEval.js')

test('WEFLOW_AUDIT_DIR 能把审计指到别处，且与读方一致', () => {
  const dir = mkdtempSync(join(tmpdir(), 'audit-dir-'))
  const saved = process.env.WEFLOW_AUDIT_DIR
  try {
    process.env.WEFLOW_AUDIT_DIR = dir
    const expected = join(dir, 'assistant_audit.log')
    assert.equal(privacyGate.auditFilePath(), expected, '写入方要用这个路径')

    const before = auditSize()
    privacyGate.audit('DIR_TEST', 7, 'probe')
    assert.ok(existsSync(expected), '审计应该真的写到这个目录')
    // **读方也必须指向同一份**：两边各算各的路径，就是这次失效的形态
    assert.ok(auditSize() > before, '读方要看到写入方的增长')
    const tail = readAuditSince(before)
    assert.equal(tail.cloudCalls, 0, '这条不是 CLOUD_CALL')
    assert.match(readFileSync(expected, 'utf8'), /DIR_TEST/)
  } finally {
    if (saved === undefined) delete process.env.WEFLOW_AUDIT_DIR
    else process.env.WEFLOW_AUDIT_DIR = saved
    rmSync(dir, { recursive: true, force: true })
  }
})

test('readAuditSince 能读出 TOOL: 与 CLOUD_CALL（这两个是评测的全部观测源）', () => {
  const dir = mkdtempSync(join(tmpdir(), 'audit-read-'))
  const saved = process.env.WEFLOW_AUDIT_DIR
  try {
    process.env.WEFLOW_AUDIT_DIR = dir
    const offset = auditSize()
    privacyGate.audit('TOOL:list_sessions', 12)
    privacyGate.audit('CLOUD_CALL', 900, 'api.example')
    privacyGate.audit('TOOL:get_messages', 40)
    const got = readAuditSince(offset)
    assert.deepEqual(got.tools, ['list_sessions', 'get_messages'])
    assert.equal(got.cloudCalls, 1)
  } finally {
    if (saved === undefined) delete process.env.WEFLOW_AUDIT_DIR
    else process.env.WEFLOW_AUDIT_DIR = saved
    rmSync(dir, { recursive: true, force: true })
  }
})

test('没有审计文件时读方返回空，不抛', () => {
  const dir = mkdtempSync(join(tmpdir(), 'audit-empty-'))
  const saved = process.env.WEFLOW_AUDIT_DIR
  try {
    process.env.WEFLOW_AUDIT_DIR = dir
    assert.deepEqual(readAuditSince(0), { tools: [], cloudCalls: 0 })
  } finally {
    if (saved === undefined) delete process.env.WEFLOW_AUDIT_DIR
    else process.env.WEFLOW_AUDIT_DIR = saved
    rmSync(dir, { recursive: true, force: true })
  }
})
