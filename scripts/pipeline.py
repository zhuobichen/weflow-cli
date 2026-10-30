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
from datetime import datetime, timedelta
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
    parser.add_argument('--yesterday', action='store_true',
                        help='跑**昨天**（等价于 --date 昨天）。给计划任务用的：公众号发到 24:00 才停，'
                             '当晚跑的那一天永远不全；而"跑昨天"与时刻无关 —— 机器当时在睡觉、'
                             '起来补跑，结果完全相同（见 OPERATIONS「让它每天自己跑」）')
    parser.add_argument('--interest', default='AI', help='兴趣主题（默认 AI）')
    parser.add_argument('--wiki-limit', type=int, default=20, help='概念编译数量（默认 20）')
    parser.add_argument('--skip-classify', action='store_true', help='跳过后处理')
    parser.add_argument('--with-wiki', action='store_true',
                        help='把概念页写进 Obsidian Vault。**默认不做** —— Obsidian 自带的图谱\n'
                             '视图是实时的，写一次 vault 图谱就重画一次，所以只有你明确要它重建时才写')
    parser.add_argument('--with-vault', action='store_true',
                        help='把当天的内容副本同步进 Obsidian Vault（Sources/WeChat/<日期>/）。**默认不做**')
    parser.add_argument('--skip-html', action='store_true', help='跳过 HTML 生成')
    parser.add_argument('--skip-ai-report', action='store_true', help='跳过 AI 深度阅读报告')
    parser.add_argument('--no-ai', action='store_true', help='关闭所有 AI 调用，但保留抓取和本地输出')
    parser.add_argument('--no-summary', action='store_true',
                        help='biz_daily 只做判断不做生成（不调 LLM 写摘要/标签/简报，'
                             '主题与相关度仍由 Jev 判断），因此**不需要 DeepSeek key**。'
                             '注意：下游步骤（行动建议/概念编译/AI 报告）仍会用 LLM，'
                             '要全关请再加 --skip-classify --skip-ai-report')
    parser.add_argument('--ai-report-range', type=int, default=1, help='AI 报告覆盖最近 N 天（默认 1=仅当天）')
    parser.add_argument('--prefetch-bodies', nargs='?', type=int, const=8, default=0, metavar='N',
                        help='只转发给 biz_daily：Phase 1 之前先并发把这一天的正文抓进缓存'
                             '（N=并发数，默认 8），让 Phase 1 走缓存命中、不再逐篇 sleep。'
                             '450 篇约 75 分钟 → 秒级；对上游的请求次数不变（见 biz_daily.prefetch_bodies）')
    parser.add_argument('--source', action='append', default=[], metavar='NAME',
                        help='仅处理指定公众号，可重复或用逗号分隔')
    parser.add_argument('--rss-feed', action='append', default=None, metavar='URL',
                      help='外部 RSS/Atom 订阅源（可重复），原样转发给 biz_daily：'
                           '微信 4.2.0.11 起绝大多数公众号推送不再落库，用标准订阅源补缺口')
    parser.add_argument('--rss-feeds-file', default=None, metavar='PATH',
                        help='订阅源配置文件，原样转发给 biz_daily（默认 output/rss-feeds.json）')
    parser.add_argument('--rss-opml', default=None, metavar='PATH',
                        help='OPML 订阅文件，原样转发给 biz_daily：一次导入几十上百个源')
    args = parser.parse_args()

    if args.yesterday:
        if args.date:
            print('[ERROR] --date 与 --yesterday 不能同时给（前者是明确的一天，后者是"昨天"）')
            sys.exit(1)
        args.date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    sys.path.insert(0, SCRIPTS_DIR)
    from _utils import load_config, get_api_key
    config = load_config()
    if str(config.get('dailyAiEnabled', 'true')).strip().lower() == 'false':
        args.no_ai = True

    # api_key：本地/ollama 引擎不需要；云端需要。
    # `--no-summary` 时 biz_daily 不生成任何文字，所以这一步不需要 key——但**下游步骤
    # 仍需要**，所以只在"下游也全关"时才整段跳过校验，否则照旧检查（免得后面某一步
    # 在跑到一半时才发现没 key）。
    #
    # **只算真的会调 LLM 的那两步**（行动建议、概念编译）：`generate_ai_report.py` 虽然
    # 叫 AI 报告，但一次 LLM 都不调（它读已落盘的概率与判断），把它算进来会让
    # `--no-summary --skip-classify` 仍然索要 DeepSeek key——那正是这条
    # 路要摆脱的东西。
    api_key = args.api_key or ''
    downstream_ai = (not args.skip_classify) or args.with_wiki
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
    for feed_url in (args.rss_feed or []):
        step1_args += ['--rss-feed', feed_url]
    if args.rss_feeds_file:
        step1_args += ['--rss-feeds-file', args.rss_feeds_file]
    if args.rss_opml:
        step1_args += ['--rss-opml', args.rss_opml]
    if args.no_ai:
        step1_args += ['--no-ai']
    if args.no_summary and not args.no_ai:
        # 只对 biz_daily 转发：它跳过摘要/简报的生成，判断仍走 Jev。
        step1_args += ['--no-summary']
        if downstream_ai:
            print('  [提示] --no-summary 只关掉本步骤的 LLM 生成；下游仍会调 LLM 的步骤：'
                  '%s。要全关请补上对应 --skip-*'
                  % '、'.join(n for n, skipped in (('classify_daily（行动建议）', args.skip_classify),
                                                   ('compile_wiki（概念编译）', not args.with_wiki))
                              if not skipped))
    if args.prefetch_bodies:
        # 原样转发：`--prefetch-bodies` 不带数字时 biz_daily 用自己的默认并发数，
        # 所以这里不加数字（免得两处默认值各写一份、迟早漂移）。
        step1_args += ['--prefetch-bodies']
        if args.prefetch_bodies != 8:            # 显式给了数字才转发数字
            step1_args += [str(args.prefetch_bodies)]
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

    # Step 3: Vault sync（**默认不做**，见 --help）
    if args.with_vault:
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

    # Step 4: wiki compile（**默认不做**，见 --help）
    if args.with_wiki and not args.no_ai:
        step3_args = [
            os.path.join(SCRIPTS_DIR, 'compile_wiki.py'),
            '--limit', str(args.wiki_limit),
        ]
        run_step('wiki compile — 概念编译', step3_args)

    # 默认不写 Vault：**Obsidian 自带的图谱视图是实时的** —— 它盯着 vault 里的文件，文件一变
    # 就重画，所以"内容一更新图谱就重建"只在"自动改 vault"时发生。要它变，显式加那两个开关。
    if not args.with_vault or not args.with_wiki:
        missing = [name for name, on in (('--with-vault（当天内容副本）', args.with_vault),
                                        ('--with-wiki（概念页）', args.with_wiki)) if not on]
        print('\n  Obsidian 那边没有动：%s。要写进去就显式加上 —— 图谱只在你要它重建时才变。'
              % '、'.join(missing))

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
