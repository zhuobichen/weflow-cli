"""测试用的临时目录：**绝不允许落在仓库里**。

为什么需要它：`tempfile.mkdtemp()` 在"系统临时目录不可写"时会**静默退回当前目录**
（Python 的候选列表最后一项是 `os.getcwd()`）。本机沙箱就是这样——2026-11-01 一次清理
发现仓库根多了 **783** 个 `tmp*` / `weflow-*` / `qq-idx-*` 目录，占用约 975 MB，
而测试当时全是绿的：没有任何东西会告诉你它们建错了地方。

这个包装器把"默默污染仓库"变成一次响亮的失败，并告诉你该怎么修（设 TMPDIR/TEMP）。
"""
import pathlib
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent


import os


def _explicit_temp_dirs() -> list[pathlib.Path]:
    """调用方**显式**指定的临时目录（TMPDIR/TEMP/TMP）。

    仓库文档里给 Windows 的做法就是把 TEMP 指到 `tmp_test`（在仓库内）——
    那是**刻意**的，不该被当成"悄悄退回 cwd"。所以：落在这些目录下就放行。
    """
    out = []
    for key in ('TMPDIR', 'TEMP', 'TMP'):
        value = os.environ.get(key)
        if value:
            try:
                out.append(pathlib.Path(value).resolve())
            except OSError:
                pass
    return out


def mkdtemp(prefix: str) -> str:
    """建临时目录；如果它**悄悄**落在仓库里就抛错（而不是继续往仓库里写）。"""
    created = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    resolved = created.resolve()
    for base in _explicit_temp_dirs():
        try:
            resolved.relative_to(base)
            return str(created)   # 显式配置的临时目录（即便在仓库内）→ 放行
        except ValueError:
            continue
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError:
        return str(created)
    # 落在仓库里：系统临时目录不可用，tempfile 退回了 cwd。
    try:
        created.rmdir()
    except OSError:
        pass
    raise RuntimeError(
        f'临时目录落在了仓库里（{created}）——说明系统临时目录不可写，'
        f'tempfile 退回了当前目录。请设置 TMPDIR（Linux/macOS）或 TEMP（Windows）'
        f'到一个可写目录后重跑。'
    )
