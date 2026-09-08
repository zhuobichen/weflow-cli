#!/usr/bin/env python3
"""
同步收藏夹 — 根据 .fav_state.json 将收藏文章 symlink 到 收藏/ 文件夹。

用法:
  python scripts/sync_fav.py --date 2026-05-19
  python scripts/sync_fav.py --date 2026-05-19 --add "AI/某文章.md" --remove "学术/某文章.md"

.fav_state.json 格式: ["AI/文章1.md", "学术/文章2.md", ...]
与 HTML localStorage 的 weflow_fav_{date} 格式一致。
"""

import sys, os, json, re
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.join(os.path.dirname(SCRIPTS_DIR), 'output', 'biz-daily')


def resolve_date_dir(date_str: str, source_root: str) -> Path:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        raise ValueError('日期必须是 YYYY-MM-DD')
    root = Path(source_root).resolve()
    date_dir = (root / date_str).resolve()
    if date_dir.parent != root:
        raise ValueError('日期目录超出日报根目录')
    return date_dir


def resolve_favorite_source(date_dir: Path, relative_path: object) -> tuple[str, Path] | None:
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    candidate = Path(relative_path)
    if candidate.is_absolute() or candidate.suffix.lower() != '.md':
        return None
    source = (date_dir / candidate).resolve()
    try:
        normalized = source.relative_to(date_dir)
    except ValueError:
        return None
    if not normalized.parts or normalized.parts[0] == '收藏' or not source.is_file():
        return None
    return normalized.as_posix(), source


def sync_favorites(date_str: str, source_root: str = SOURCE_ROOT) -> bool:
    """读取 .fav_state.json 并同步 收藏/ 文件夹中的 symlink。"""
    try:
        date_dir = resolve_date_dir(date_str, source_root)
    except ValueError as error:
        print(f'[ERROR] {error}')
        return False
    if not date_dir.is_dir():
        print(f'[ERROR] 目录不存在: {date_dir}')
        return False

    fav_dir = date_dir / '收藏'
    fav_state_file = date_dir / '.fav_state.json'

    # 读取收藏列表
    if fav_state_file.exists():
        try:
            with open(fav_state_file, 'r', encoding='utf-8') as f:
                fav_list = json.load(f)
        except Exception:
            print(f'[ERROR] 无法解析 .fav_state.json')
            return False
    else:
        fav_list = []

    if not fav_list:
        print('收藏列表为空，清理 收藏/ 文件夹...')
        if fav_dir.is_dir():
            for link in fav_dir.iterdir():
                if link.is_symlink() or link.is_file():
                    link.unlink()
                    print(f'  移除: {link.name}')
        return True

    # 创建收藏文件夹
    fav_dir.mkdir(parents=True, exist_ok=True)

    # 构建期望的链接集合
    desired: dict[str, Path] = {}
    for rel_path in fav_list:
        resolved = resolve_favorite_source(date_dir, rel_path)
        if resolved:
            normalized, source = resolved
            desired[normalized] = source
        else:
            print('[WARN] 忽略无效或越界的收藏项')

    # 清理不在列表中的旧链接
    existing = set()
    for item in fav_dir.iterdir():
        if item.is_symlink():
            existing.add(item.name)
            if item.name not in {source.name for source in desired.values()}:
                item.unlink()
                print(f'  移除: {item.name}')
        elif item.is_file():
            # 非 symlink 的文件也清理
            existing.add(item.name)
            if item.name not in {source.name for source in desired.values()}:
                item.unlink()
                print(f'  移除: {item.name}')

    # 创建缺失的 symlink
    for src in desired.values():
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
    return True


def manage_fav(date_str: str, add: list = None, remove: list = None, source_root: str = SOURCE_ROOT) -> bool:
    """手动添加/移除收藏项。"""
    try:
        date_dir = resolve_date_dir(date_str, source_root)
    except ValueError as error:
        print(f'[ERROR] {error}')
        return False
    if not date_dir.is_dir():
        print(f'[ERROR] 日报目录不存在')
        return False
    fav_state_file = date_dir / '.fav_state.json'

    fav_list = []
    if fav_state_file.exists():
        try:
            with open(fav_state_file, 'r', encoding='utf-8') as f:
                fav_list = json.load(f)
        except Exception:
            pass

    changed = False
    invalid_input = False
    if add:
        for item in add:
            resolved = resolve_favorite_source(date_dir, item)
            if not resolved:
                print('[WARN] 忽略无效或越界的收藏项')
                invalid_input = True
                continue
            normalized, _ = resolved
            if normalized not in fav_list:
                fav_list.append(normalized)
                print(f'  收藏: {normalized}')
                changed = True

    if remove:
        for item in remove:
            candidate = Path(item)
            if candidate.is_absolute() or candidate.suffix.lower() != '.md' or '..' in candidate.parts:
                print('[WARN] 忽略无效或越界的收藏项')
                invalid_input = True
                continue
            normalized = candidate.as_posix()
            if normalized in fav_list:
                fav_list.remove(normalized)
                print(f'  取消收藏: {normalized}')
                changed = True

    if changed:
        with open(fav_state_file, 'w', encoding='utf-8') as f:
            json.dump(fav_list, f, ensure_ascii=False, indent=2)
        print(f'已更新 .fav_state.json ({len(fav_list)} 篇收藏)')

    synced = sync_favorites(date_str, source_root)
    return not invalid_input and synced


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    parser = argparse.ArgumentParser(description='同步收藏夹到 收藏/ 文件夹')
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--root', default=SOURCE_ROOT, help='日报输出根目录')
    parser.add_argument('--add', nargs='*', help='添加收藏 (相对路径)')
    parser.add_argument('--remove', nargs='*', help='取消收藏 (相对路径)')
    args = parser.parse_args()

    if args.add or args.remove:
        if not manage_fav(args.date, args.add, args.remove, args.root):
            sys.exit(1)
    else:
        if not sync_favorites(args.date, args.root):
            sys.exit(1)
        print(f'✓ 收藏同步完成')


if __name__ == '__main__':
    main()
