#!/usr/bin/env python3
"""
端到端流水线 — biz_daily → classify_daily → wiki compile 一键串联。

用法:
  python scripts/pipeline.py --api-key <key> [--date 2026-05-15] [--interest AI] [--wiki-limit 20]
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
    result = subprocess.run([PYTHON] + args, cwd=os.path.dirname(SCRIPTS_DIR))
    if result.returncode != 0:
        print(f'\n[FAIL] {name} 失败 (exit={result.returncode})')
        return False
    return True


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='端到端公众号日报流水线')
    parser.add_argument('--api-key', help='DeepSeek API key（或环境变量 DEEPSEEK_API_KEY）')
    parser.add_argument('--date', help='日期 YYYY-MM-DD')
    parser.add_argument('--interest', default='AI', help='兴趣主题（默认 AI）')
    parser.add_argument('--wiki-limit', type=int, default=20, help='概念编译数量（默认 20）')
    parser.add_argument('--skip-classify', action='store_true', help='跳过后处理')
    parser.add_argument('--skip-wiki', action='store_true', help='跳过概念编译')
    parser.add_argument('--skip-vault', action='store_true', help='跳过 Vault 同步')
    parser.add_argument('--skip-html', action='store_true', help='跳过 HTML 生成')
    args = parser.parse_args()

    sys.path.insert(0, SCRIPTS_DIR)
    from _utils import load_config
    config = load_config()
    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '') or config.get('deepseekApiKey', '')
    if not api_key:
        print('[ERROR] 需要 DeepSeek API key。请通过 --api-key、环境变量 DEEPSEEK_API_KEY 或 ~/.weflow-cli/config.json 中的 deepseekApiKey 提供')
        sys.exit(1)

    started = time.time()

    # Step 1: biz_daily
    step1_args = [os.path.join(SCRIPTS_DIR, 'biz_daily.py'), '--api-key', api_key]
    if args.date:
        step1_args += ['--date', args.date]
    if not run_step('biz_daily — 抓取+摘要', step1_args):
        sys.exit(1)

    # Step 2: classify_daily（可选）
    if not args.skip_classify:
        step2_args = [os.path.join(SCRIPTS_DIR, 'classify_daily.py'), '--api-key', api_key, '--interest', args.interest]
        if args.date:
            step2_args.insert(1, args.date)
        run_step('classify_daily — 后处理', step2_args)

    # Step 3: Vault sync（可选）
    if not args.skip_vault:
        date_str = args.date or time.strftime('%Y-%m-%d')
        source_dir = os.path.join(SOURCE_ROOT, date_str)
        vault_dir = os.path.join(os.path.dirname(SCRIPTS_DIR), 'output', 'wechat-vault', 'Sources', 'WeChat', date_str)
        if os.path.exists(source_dir):
            if os.path.exists(vault_dir):
                shutil.rmtree(vault_dir)
            shutil.copytree(source_dir, vault_dir)
            file_count = sum(1 for _ in Path(vault_dir).rglob('*.md'))
            print(f'\n  Vault 同步: {file_count} 个文件 → {vault_dir}')

    # Step 4: wiki compile（可选）
    if not args.skip_wiki:
        step3_args = [
            os.path.join(SCRIPTS_DIR, 'compile_wiki.py'),
            '--api-key', api_key,
            '--limit', str(args.wiki_limit),
        ]
        run_step('wiki compile — 概念编译', step3_args)

    # Step 5: HTML 生成（可选）
    if not args.skip_html:
        date_str = args.date or time.strftime('%Y-%m-%d')
        run_step('generate_html', [os.path.join(SCRIPTS_DIR, 'generate_html.py'), '--date', date_str])

    # Step 6: 双向链接增强
    date_str = args.date or time.strftime('%Y-%m-%d')
    run_step('enrich_backlinks — 双向链接', [os.path.join(SCRIPTS_DIR, 'enrich_backlinks.py'), '--date', date_str])

    # Step 7: 阅读笔记生成
    run_step('create_reading_notes — 阅读笔记', [os.path.join(SCRIPTS_DIR, 'create_reading_notes.py'), '--date', date_str])

    elapsed = time.time() - started
    print(f'\n{"="*50}')
    print(f'  流水线完成！耗时 {elapsed/60:.1f} 分钟')
    print(f'{"="*50}')


if __name__ == '__main__':
    main()
