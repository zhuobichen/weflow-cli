/**
 * "还没配置"时到底该让人做什么——**按平台给不同答案**。
 *
 * Windows 上 `init` 能自动提密钥，所以"运行 init"是对的。
 * Linux 4.1.12.26+ 换了密钥方案（共享口令 + 每分片派生），`init` 只能发现目录与账号，
 * 取密钥那一步**必然**停在"未匹配到数据库密钥"。
 *
 * 2026-11-02 在 Ubuntu 上实测：`weflow-cli sessions` 直接甩一句"请先运行 weflow-cli init"，
 * 用户照着做会再撞一次墙——同一类"提示与实现不一致"（issue #8 也是这么来的）。
 */
export function unconfiguredHint(): string {
  if (process.platform === 'linux') {
    return [
      '还没有可用的数据库密钥（Linux 版微信 4.1.12.26+ 不再支持自动提取）。三条路：',
      '  1. 有口令: weflow-cli fav set-key --passphrase <64位hex>  然后 weflow-cli init',
      '  2. 先验证口令对不对: python3 scripts/nt_decrypt.py verify-passphrase-all --passphrase <64位hex>',
      '  3. 不碰本地库: weflow-cli daily --rss-feed <地址>  /  --articles-json <文件>',
    ].join('\n')
  }
  return '请先运行 weflow-cli init'
}

/** 给 JSON 输出用的单行版本（多行塞进 JSON 反而不方便消费者）。 */
export function unconfiguredHintInline(): string {
  return unconfiguredHint().split('\n').map((line) => line.trim()).join('  ')
}
