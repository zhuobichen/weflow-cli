#!/usr/bin/env python3
"""
端到端流水线 — biz_daily → classify_daily → wiki compile → AI report 一键串联。

默认策略：
- AI 推理优先走本地（Ollama/LM Studio/Claude Code 本地服务），不需要 API key
- 如果没有本地服务，再显式指定 --engine deepseek/claude

用法:
  python scripts/pipeline.py                     # 全部步骤，默认走本地推理
  python scripts/pipeline.py --engine deepseek --api-key <key>
  python scripts/pipeline.py --engine claude --api-key <key>
  python scripts/pipeline.py --date 2026-06-09 --skip-classify
"""
import sys, os, subprocess, time, shutil
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = 'output/biz-daily'
PYTHON = sys.executable


def run_step(name: str, args: list[str]) -> bool:
    """运行一个步骤，返回是否成功。"""
    print(f'\n{"="*50}')
    print(f'  Step: {name}')
    print(f'{"="*50}')
    result = subprocess.run([PYTHON, '-u'] + args, cwd=os.path.dirname(SCRIPTS_DIR))
    if result.returncode != 0:
        print(f'\n[FAIL] {name} 失败 (exit={result.returncode})')
        return False
    return True


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='端到端公众号日报流水线')
    parser.add_argument('--api-key', help='DeepSeek API key（也可通过环境变量 DEEPSEEK_API_KEY 或 ~/.weflow-cli/config.json 提供）')
    parser.add_argument('--engine', default='deepseek',
                        help='AI 引擎: deepseek(默认) / claude / ollama / local')
    parser.add_argument('--date', help='日期 YYYY-MM-DD')
    parser.add_argument('--interest', default='AI', help='兴趣主题（默认 AI）')
    parser.add_argument('--wiki-limit', type=int, default=20, help='概念编译数量（默认 20）')
    parser.add_argument('--skip-classify', action='store_true', help='跳过后处理')
    parser.add_argument('--skip-wiki', action='store_true', help='跳过概念编译')
    parser.add_argument('--skip-vault', action='store_true', help='跳过 Vault 同步')
    parser.add_argument('--skip-html', action='store_true', help='跳过 HTML 生成')
    parser.add_argument('--skip-ai-report', action='store_true', help='跳过 AI 深度阅读报告')
    parser.add_argument('--no-ai', action='store_true', help='关闭所有 AI 调用，但保留抓取和本地输出')
    parser.add_argument('--no-summary', action='store_true',
                        help='biz_daily 只做判断不做生成（不调 LLM 写摘要/标签/简报，'
                             '主题与相关度仍由 Jev 判断），因此**不需要 DeepSeek key**。'
                             '注意：下游步骤（行动建议/概念编译/AI 报告）仍会用 LLM，'
                             '要全关请再加 --skip-classify --skip-wiki --skip-ai-report')
    parser.add_argument('--ai-report-range', type=int, default=1, help='AI 报告覆盖最近 N 天（默认 1=仅当天）')
    parser.add_argument('--source', action='append', default=[], metavar='NAME',
                        help='仅处理指定公众号，可重复或用逗号分隔')
    args = parser.parse_args()

    sys.path.insert(0, SCRIPTS_DIR)
    from _utils import load_config, get_api_key
    config = load_config()
    if str(config.get('dailyAiEnabled', 'true')).strip().lower() == 'false':
        args.no_ai = True

    # api_key：本地/ollama 引擎不需要；云端需要。
    # `--no-summary` 时 biz_daily 不生成任何文字，所以这一步不需要 key——但**下游步骤
    # 仍需要**，所以只在"下游也全关"时才整段跳过校验，否则照旧检查（免得后面某一步
    # 在跑到一半时才发现没 key）。
    api_key = args.api_key or ''
    downstream_ai = not (args.skip_classify and args.skip_wiki and args.skip_ai_report)
    needs_key = args.engine in ('deepseek', 'claude') and not args.no_ai and (
        not args.no_summary or downstream_ai)
    if needs_key and not api_key:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '') or get_api_key(config)
        if not api_key:
            print(f'[ERROR] --engine {args.engine} 需要 API key。请通过 --api-key、'
                  f'环境变量 DEEPSEEK_API_KEY 或 ~/.weflow-cli/config.json 提供')
            sys.exit(1)

    if api_key:
        os.environ['DEEPSEEK_API_KEY'] = api_key

    started = time.time()

    # Step 1: biz_daily
    step1_args = [os.path.join(SCRIPTS_DIR, 'biz_daily.py'), '--engine', args.engine]
    if args.date:
        step1_args += ['--date', args.date]
    for source in args.source:
        step1_args += ['--source', source]
    if args.no_ai:
        step1_args += ['--no-ai']
    if args.no_summary and not args.no_ai:
        # 只对 biz_daily 转发：它跳过摘要/简报的生成，判断仍走 Jev。
        step1_args += ['--no-summary']
        if downstream_ai:
            print('  [提示] --no-summary 只关掉本步骤的 LLM 生成；下游步骤仍会用 LLM，'
                  '要全关请加 --skip-classify --skip-wiki --skip-ai-report')
    if not run_step('biz_daily — 抓取+摘要', step1_args):
        sys.exit(1)

    # Step 2: classify_daily（可选）
    if not args.skip_classify and not args.no_ai:
        step2_args = [
            os.path.join(SCRIPTS_DIR, 'classify_daily.py'),
            '--engine', args.engine,
        ]
        step2_args += ['--interest', args.interest]
        if args.date:
            step2_args.insert(1, args.date)
        if not run_step('classify_daily — 后处理', step2_args):
            print('[WARN] classify_daily 失败，继续后续步骤')

    # Step 3: Vault sync（可选）
    if not args.skip_vault:
        date_str = args.date or time.strftime('%Y-%m-%d')
        source_dir = os.path.join(SOURCE_ROOT, date_str)
        vault_dir = os.path.join(os.path.dirname(SCRIPTS_DIR), 'output', 'wechat-vault',
                                 'Sources', 'WeChat', date_str)
        if os.path.exists(source_dir):
            if os.path.exists(vault_dir):
                shutil.rmtree(vault_dir)
            shutil.copytree(source_dir, vault_dir)
            file_count = sum(1 for _ in Path(vault_dir).rglob('*.md'))
            print(f'\n  Vault 同步: {file_count} 个文件 → {vault_dir}')

    # Step 4: wiki compile（可选）
    if not args.skip_wiki and not args.no_ai:
        step3_args = [
            os.path.join(SCRIPTS_DIR, 'compile_wiki.py'),
            '--limit', str(args.wiki_limit),
        ]
        run_step('wiki compile — 概念编译', step3_args)

    # Step 5: HTML 生成（可选）
    if not args.skip_html:
        date_str = args.date or time.strftime('%Y-%m-%d')
        run_step('generate_html',
                 [os.path.join(SCRIPTS_DIR, 'generate_html.py'), '--date', date_str])

    # Step 6: 双向链接增强
    date_str = args.date or time.strftime('%Y-%m-%d')
    run_step('enrich_backlinks — 双向链接',
             [os.path.join(SCRIPTS_DIR, 'enrich_backlinks.py'), '--date', date_str])

    # Step 7: 阅读笔记生成
    run_step('create_reading_notes — 阅读笔记',
             [os.path.join(SCRIPTS_DIR, 'create_reading_notes.py'), '--date', date_str])

    # Step 8: AI 深度阅读报告
    if not args.skip_ai_report and not args.no_ai:
        step8_args = [
            os.path.join(SCRIPTS_DIR, 'generate_ai_report.py'),
            '--engine', args.engine,
            '--range', str(args.ai_report_range),
        ]
        if args.date and args.ai_report_range == 1:
            step8_args += ['--date', args.date]
        run_step('generate_ai_report — AI 阅读日报', step8_args)

    elapsed = time.time() - started
    print(f'\n{"="*50}')
    print(f'  流水线完成！耗时 {elapsed/60:.1f} 分钟')
    print(f'{"="*50}')


if __name__ == '__main__':
    main()
