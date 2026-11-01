#!/bin/sh
# 把 systemd 单元装到用户级目录（`~/.config/systemd/user/`），替换成真实路径。
#
# 为什么不直接把单元文件放进 `deploy/systemd/` 让用户自己拷：
#   systemd 的 `ExecStart=` / `WorkingDirectory=` **不支持**环境变量展开，
#   路径必须写死。用占位符 + 安装脚本，才能既"跟着仓库位置走"又让
#   `systemd-analyze verify` 看到一个合法的绝对路径。
#
# 用法：
#   ./deploy/systemd/install.sh              # 只装、不启用
#   ./deploy/systemd/install.sh --enable     # 装好并启用（助手 + 日报定时器）
set -eu

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$SELF_DIR/../.." && pwd)
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# `weflow-cli` 可执行文件：优先用 PATH 上的，找不到就用仓库里的 dist 入口
if command -v weflow-cli >/dev/null 2>&1; then
  CLI=$(command -v weflow-cli)
elif [ -f "$REPO/dist/bin/weflow-cli.js" ]; then
  CLI=$(command -v node || true)
  if [ -z "$CLI" ]; then
    echo "找不到 node，也找不到 weflow-cli" >&2
    exit 1
  fi
  CLI="$CLI $REPO/dist/bin/weflow-cli.js"
else
  echo "找不到 weflow-cli（PATH 上没有，$REPO/dist 也没有）" >&2
  exit 1
fi

mkdir -p "$DEST"
for unit in weflow-assistant.service weflow-daily@.service weflow-daily.timer; do
  sed -e "s|__WEFLOW_REPO__|$REPO|g" -e "s|__WEFLOW_CLI__|$CLI|g" \
    "$SELF_DIR/$unit" > "$DEST/$unit"
  echo "  已写入 $DEST/$unit"
done

# 用户级 systemd 需要 daemon-reload 才看得见新单元；容器/无 systemd 时忽略失败
systemctl --user daemon-reload >/dev/null 2>&1 || true

if [ "${1:-}" = "--enable" ]; then
  systemctl --user enable --now weflow-assistant.service || true
  systemctl --user enable --now weflow-daily@noon.timer || true
  systemctl --user enable --now weflow-daily@night.timer || true
  echo "  已启用：weflow-assistant.service、weflow-daily@noon/night.timer"
fi

cat <<EOF
装好了。常用命令：
  systemctl --user status weflow-assistant
  journalctl --user -u weflow-assistant -f
  systemctl --user list-timers 'weflow-daily*'
EOF
