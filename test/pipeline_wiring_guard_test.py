#!/usr/bin/env python3
"""守卫：pipeline 里的脚本必须按**共享约定**接线，不许各写一份。

两件事，每件都对应一次连续多天的静默失效：

## 1. 输出根必须走 `_utils.output_root()`

`classify_daily.py` 写的是 `OUTPUT_ROOT = 'output/biz-daily'`（相对路径），
`generate_ai_report.py` 写的是包目录下的 `output/`。而全局安装时（Linux 的
`/usr/lib/node_modules`）包目录属于 root，真实产物在 `~/.weflow-cli/output`——
两个脚本因此一个都读不到。

后果不是"少做一步"，是**三步全没了**，而每天照样发报告、退出码 0：

    classify_daily      文章 0 篇 → 建目录时 FileNotFoundError → exit 1
                        广告清洗、深度解析、行动建议 全部没跑
    generate_ai_report  读不到文章 → "❌ 未找到 … 的文章" → exit 1

pipeline 只打一句 `[WARN] … 失败，继续后续步骤`。2026-11-16~19 连续四天如此。

## 2. AI key 必须走 `_utils.get_api_key(config)`

`generate_ai_report.py` 只读 `DEEPSEEK_API_KEY` 环境变量，而配了 `aiBaseUrl`
（DashScope）时 key 在配置的 `aiApiKey` 里、环境变量通常是空的 → 空 key →
`HTTP Error 401` → 报告照样落盘一份**只有骨架**的版本。
这与 2026-11-15 那次日报 401 是同一类：key 解析分散在多处，改了一处漏了另一处。

判据是静态扫源码：不 import 这些脚本（它们 import 时会读配置、建目录）。
"""
import io
import pathlib
import re
import tokenize
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'

#: 必须用 output_root() 的脚本：它们都读或写 output/biz-daily 或 output/ai-reports
MUST_USE_OUTPUT_ROOT = (
    'biz_daily.py',
    'classify_daily.py',
    'generate_ai_report.py',
)

#: 会创建 AI 引擎（因此需要 key）的脚本
MUST_RESOLVE_API_KEY = (
    'classify_daily.py',
    'generate_ai_report.py',
)

#: 写死的形态：相对路径字面量，或拼在包目录（ROOT_DIR / SCRIPT_DIR）下面
HARDCODED = (
    re.compile(r"""=\s*['"]output/biz-daily['"]"""),
    re.compile(r"""os\.path\.join\(\s*(ROOT_DIR|SCRIPT_DIR|PROJECT_ROOT)\s*,\s*['"]output['"]"""),
    re.compile(r"""Path\(\s*['"]output/biz-daily['"]\s*\)"""),
)

#: 只从环境变量取 key 的形态（且没有 get_api_key 兜底）
ENV_ONLY_KEY = re.compile(r"""os\.environ(?:\.get)?\(\s*['"]DEEPSEEK_API_KEY['"]""")


def strip_comments(src: str) -> str:
    """把 `#` 注释替换成空格，**保留行号**。

    不这么做，解释「当初为什么这样写错」的注释本身会被当成违规——第一版这条守卫
    就红在自己那段注释上。用 tokenize 而不是正则，是因为正则分不清字符串里的 `#`。
    """
    lines = src.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            row, col = tok.start
            end_row, end_col = tok.end
            if row == end_row and 1 <= row <= len(lines):
                line = lines[row - 1]
                lines[row - 1] = line[:col] + ' ' * (end_col - col) + line[end_col:]
    except (tokenize.TokenError, IndentationError):
        return src
    return ''.join(lines)


class OutputRootGuardTests(unittest.TestCase):
    def test_output_writing_scripts_use_output_root(self):
        offenders = []
        for name in MUST_USE_OUTPUT_ROOT:
            raw = (SCRIPTS / name).read_text(encoding='utf-8')
            if 'output_root' not in raw:
                offenders.append(f'{name}：没引用 output_root()')
                continue
            src = strip_comments(raw)
            for pattern in HARDCODED:
                for m in pattern.finditer(src):
                    line = src[:m.start()].count('\n') + 1
                    offenders.append(f'{name}:{line} → {m.group(0).strip()}')
        self.assertEqual(
            offenders, [],
            '会读写 output/ 的脚本必须走 _utils.output_root()（全局安装时包目录不可写，'
            '写死路径会让这一步每天静默失败）：\n  ' + '\n  '.join(offenders))

    def test_guard_ignores_comments_but_catches_real_code(self):
        """反向对照：注释里提到旧写法不算违规，真代码里写了才算。"""
        commented = "OUTPUT_ROOT = computed()\n# 以前是 OUTPUT_ROOT = 'output/biz-daily'\n"
        self.assertEqual([m for p in HARDCODED for m in p.finditer(strip_comments(commented))], [])
        real = "OUTPUT_ROOT = 'output/biz-daily'\n"
        self.assertTrue(any(p.search(strip_comments(real)) for p in HARDCODED))
        real2 = "SOURCE = os.path.join(ROOT_DIR, 'output', 'biz-daily')\n"
        self.assertTrue(any(p.search(strip_comments(real2)) for p in HARDCODED))


class ApiKeyResolutionTests(unittest.TestCase):
    def test_scripts_that_build_an_engine_resolve_key_via_utils(self):
        offenders = []
        for name in MUST_RESOLVE_API_KEY:
            src = strip_comments((SCRIPTS / name).read_text(encoding='utf-8'))
            if 'get_api_key' not in src:
                offenders.append(f'{name}：没有用 get_api_key()，只认环境变量会让 401 静默发生')
                continue
            for m in ENV_ONLY_KEY.finditer(src):
                # 同一行里有 get_api_key 兜底就放行（`os.environ.get(...) or get_api_key(cfg)`）
                line_start = src.rfind('\n', 0, m.start()) + 1
                line_end = src.find('\n', m.end())
                line = src[line_start:line_end if line_end != -1 else len(src)]
                if 'get_api_key' not in line:
                    lineno = src[:m.start()].count('\n') + 1
                    offenders.append(f'{name}:{lineno} → 只读 DEEPSEEK_API_KEY，没有 get_api_key 兜底')
        self.assertEqual(
            offenders, [],
            '配了 aiBaseUrl 时 key 在配置的 aiApiKey 里，环境变量通常为空——'
            '只读环境变量会拿到空 key，表现为 401 + 一份只有骨架的报告：\n  ' + '\n  '.join(offenders))

    def test_guard_catches_env_only_key(self):
        """反向对照：守卫得真能抓到。"""
        bad = "api_key = os.environ.get('DEEPSEEK_API_KEY', '')\n"
        self.assertTrue(ENV_ONLY_KEY.search(strip_comments(bad)))
        good = "api_key = os.environ.get('DEEPSEEK_API_KEY', '') or get_api_key(cfg)\n"
        m = ENV_ONLY_KEY.search(strip_comments(good))
        self.assertIn('get_api_key', good[m.start():good.find('\n', m.end())])


if __name__ == '__main__':
    unittest.main()



if __name__ == '__main__':
    unittest.main()
