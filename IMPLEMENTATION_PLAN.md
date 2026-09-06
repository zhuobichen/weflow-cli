# WeFlow CLI 实施与验证计划

> 本文件供维护 Agent 接续工作。它记录可执行的近期计划，不复制底层平台协议或真实用户数据。

## 变更前

1. 阅读 `AGENTS.md`、`docs/PROJECT_STATE.md`、`docs/DECISIONS.md`。
2. 用 `rg` 定位命令入口、服务实现、Python 脚本和现有测试。
3. 明确数据边界：用户本人授权的本地数据、是否联网、是否调用 AI、是否产生写操作。
4. 为跨平台或媒体行为准备合成夹具，不使用真实数据库、聊天或密钥。

## 实施顺序

1. 先修改最靠近真实行为的 core/service/script。
2. 在 CLI 层保持参数、退出码和错误信息可诊断且不泄露敏感值。
3. 对 AI、MCP、助手和发送能力保留显式开关、白名单和确认步骤。
4. 更新 README、操作手册、架构、项目状态或决策记录中受影响的部分。
5. 为用户可见变化追加 `CHANGELOG.md` 条目。

## 当前验证矩阵

| 范围 | 验证 |
| --- | --- |
| TypeScript | `npm run build`、`npm test` |
| Python 工具 | `python -m unittest discover -s test -p '*_test.py' -v` |
| 日报 | `daily --dry-run`、`daily --no-ai`、显式日期和来源筛选 |
| 数据目录 | 显式 `--path`、`--search-drives`、必要时 `--full-scan` |
| 导出 | 合成消息的发送者、分片隔离、图片/表情无串图 |
| 隐私 | MCP 路径校验、云端 AI 显式同意、助手默认拒绝、发送确认 |

## 完成条件

- 代码行为与 `--help`、README、操作手册一致。
- 失败路径给出下一步，不覆盖有效配置或产生未声明的网络请求。
- 回归测试通过，`git diff --check` 通过。
- Diff 中没有数据库、导出内容、密钥、token、wxid、真实路径或私密截图。
- `docs/PROJECT_STATE.md` 记录新的能力、限制和验证状态。

## 不在本计划内

个人微信账号的远程静默控制、普通群聊自动入群、非官方协议绕过，以及把 AI 结果包装成法律结论，都不属于本项目默认实施范围。
