#!/usr/bin/env bash
# 公众号日报 —— Linux/macOS 版定时任务入口（对应 daily_task.ps1）。
#
# 为什么要有这个文件：`daily_task.ps1` 只能在 Windows 上跑，而 Linux 上
# 数据目录发现、账号扫描、外部源出日报都已经可用（2026-11-01 实测），
# 缺的只是一个能被 cron / systemd timer 调用的入口。
#
# 用法：
#   ./daily_task.sh                 # catchup（默认：补昨天）
#   ./daily_task.sh noon            # 跑今天（午报）
#   ./daily_task.sh night           # 跑今天（日终；20 点前调用会自动降级为 catchup）
#   ./daily_task.sh catchup         # 补昨天
#   ./daily_task.sh noon --no-ai    # 模式之后的参数原样转发给 pipeline.py
#
# 与 PowerShell 版一致的三条约定：
#   1. 同一模式当天已跑过（存在 .noon_done/.night_done/.catchup_done）就跳过；
#   2. 每次运行的日志单独留档，不覆盖上一次失败的那份；
#   3. night 在 20 点前被调用（例如开机补跑）按 catchup 处理——否则会把
#      "刚刚开始的一天"当成闭合的一天写标记，导致真正的 23:30 那次跳过。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

mode="catchup"
if [[ $# -gt 0 && "$1" =~ ^(noon|night|catchup|day)$ ]]; then
  mode="$1"
  [[ "$mode" == "day" ]] && mode="catchup"
  shift
fi

hour="$(date +%H)"
if [[ "$mode" == "night" && $((10#$hour)) -lt 20 ]]; then
  mode="catchup"
fi

today="$(date +%Y-%m-%d)"
yesterday="$(date -d 'yesterday' +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d)"

case "$mode" in
  noon|night) target="$today" ;;
  *)          target="$yesterday" ;;
esac

suffix=""
case "$mode" in
  noon)  suffix="-noon" ;;
  night) suffix="-night" ;;
esac

out="$LOG_DIR/daily-${today}${suffix}.log"
err="$LOG_DIR/daily-${today}${suffix}.err.log"

# 同一模式已完成的标记（与 PowerShell 版同名，两者可互通）
if [[ $# -eq 0 ]]; then
  case "$mode" in
    noon)   marker=".noon_done" ;;
    night)  marker=".night_done" ;;
    *)      marker=".catchup_done" ;;
  esac
  if [[ -f "$ROOT/output/biz-daily/$target/$marker" ]]; then
    echo "===== $(date '+%Y-%m-%d %H:%M:%S')  [$mode] $target 已完成，跳过 =====" \
      >> "$LOG_DIR/daily-task.log"
    exit 0
  fi
fi

# 上一次的日志不覆盖：轮转成 .runN 再写新的
if [[ -f "$out" ]]; then
  n=1
  while [[ -f "$LOG_DIR/daily-${today}${suffix}.run${n}.log" ]]; do n=$((n + 1)); done
  mv "$out" "$LOG_DIR/daily-${today}${suffix}.run${n}.log"
  [[ -f "$err" ]] && mv "$err" "$LOG_DIR/daily-${today}${suffix}.run${n}.err.log"
fi

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

python_bin="${PYTHON:-python3}"
"$python_bin" "$SCRIPT_DIR/pipeline.py" --date "$target" --prefetch-bodies "$@" \
  > "$out" 2> "$err"
code=$?

echo "===== $(date '+%Y-%m-%d %H:%M:%S')  [$mode] $target exit=$code =====" \
  >> "$LOG_DIR/daily-task.log"
exit "$code"
