#!/usr/bin/env python3
"""
同步收藏夹 — 根据 .fav_state.json 将收藏文章 symlink 到 收藏/ 文件夹。

用法:
  python scripts/sync_fav.py --date 2026-05-19
  python scripts/sync_fav.py --date 2026-05-19 --add "AI/某文章.md" --remove "学术/某文章.md"

.fav_state.json 格式: ["AI/文章1.md", "学术/文章2.md", ...]
与 HTML localStorage 的 weflow_fav_{date} 格式一致。
"""

import sys, os, json
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.join(os.path.dirname(SCRIPTS_DIR), 'output', 'biz-daily')


def sync_favorites(date_str: str):
    """读取 .fav_state.json 并同步 收藏/ 文件夹中的 symlink。"""
    date_dir = Path(SOURCE_ROOT) / date_str
    if not date_dir.is_dir():
        print(f'[ERROR] 目录不存在: {date_dir}')
        return

    fav_dir = date_dir / '收藏'
    fav_state_file = date_dir / '.fav_state.json'

    # 读取收藏列表
    if fav_state_file.exists():
        try:
            with open(fav_state_file, 'r', encoding='utf-8') as f:
                fav_list = json.load(f)
        except Exception:
            print(f'[ERROR] 无法解析 .fav_state.json')
            return
    else:
        fav_list = []

    if not fav_list:
        print('收藏列表为空，清理 收藏/ 文件夹...')
        if fav_dir.is_dir():
            for link in fav_dir.iterdir():
                if link.is_symlink() or link.is_file():
                    link.unlink()
                    print(f'  移除: {link.name}')
        return

    # 创建收藏文件夹
    fav_dir.mkdir(parents=True, exist_ok=True)

    # 构建期望的链接集合
    desired = set()
    for rel_path in fav_list:
        src = date_dir / rel_path
        if src.exists():
            desired.add(rel_path)
        else:
            print(f'[WARN] 源文件不存在: {rel_path}')

    # 清理不在列表中的旧链接
    existing = set()
    for item in fav_dir.iterdir():
        if item.is_symlink():
            existing.add(item.name)
            if item.name not in {Path(p).name for p in desired}:
                item.unlink()
                print(f'  移除: {item.name}')
        elif item.is_file():
            # 非 symlink 的文件也清理
            existing.add(item.name)
            if item.name not in {Path(p).name for p in desired}:
                item.unlink()
                print(f'  移除: {item.name}')

    # 创建缺失的 symlink
    for rel_path in desired:
        src = date_dir / rel_path
        link = fav_dir / src.name
        if not link.exists():
            try:
                link.symlink_to(os.path.relpath(src, fav_dir))
                print(f'  添加: {src.name}')
            except OSError:
                # Windows 可能不支持 symlink，尝试复制
                import shutil
                shutil.copy2(src, link)
                print(f'  复制: {src.name}')


def manage_fav(date_str: str, add: list = None, remove: list = None):
    """手动添加/移除收藏项。"""
    date_dir = Path(SOURCE_ROOT) / date_str
    fav_state_file = date_dir / '.fav_state.json'

    fav_list = []
    if fav_state_file.exists():
        try:
            with open(fav_state_file, 'r', encoding='utf-8') as f:
                fav_list = json.load(f)
        except Exception:
            pass

    changed = False
    if add:
        for item in add:
            if item not in fav_list:
                fav_list.append(item)
                print(f'  收藏: {item}')
                changed = True

    if remove:
        for item in remove:
            if item in fav_list:
                fav_list.remove(item)
                print(f'  取消收藏: {item}')
                changed = True

    if changed:
        with open(fav_state_file, 'w', encoding='utf-8') as f:
            json.dump(fav_list, f, ensure_ascii=False, indent=2)
        print(f'已更新 .fav_state.json ({len(fav_list)} 篇收藏)')

    sync_favorites(date_str)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='同步收藏夹到 收藏/ 文件夹')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--add', nargs='*', help='添加收藏 (相对路径)')
    parser.add_argument('--remove', nargs='*', help='取消收藏 (相对路径)')
    args = parser.parse_args()

    if args.add or args.remove:
        manage_fav(args.date, args.add, args.remove)
    else:
        sync_favorites(args.date)
        print(f'✓ 收藏同步完成')


if __name__ == '__main__':
    main()
