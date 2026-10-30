# Runs a daily report pass, with everything written to logs/daily-<date>.log.
#
# Three modes, because a WeChat official-account day only fills up gradually:
#
#   daily_task.ps1 noon     -> today so far (run at 13:00)
#   daily_task.ps1 night    -> today near end of day (run at 23:30)
#   daily_task.ps1 catchup  -> yesterday after login, including posts after 23:30
#
# The night pass is incremental by design: `.run_state.json` makes biz_daily skip the
# articles the noon pass already handled, while README/index.html are rebuilt from every
# md on disk. The login catch-up closes the final half-hour and recovers missed nights.
#
# Why this wrapper exists instead of putting the command straight into the scheduled task:
#
#  1. The task must not depend on a build artifact. It calls the python layer
#     (scripts/pipeline.py) directly, which is the same source the repo builds from.
#  2. A task whose output goes nowhere answers "it failed" and nothing else. One log
#     file per run, so "why was yesterday short?" has an answer.
#  3. Redirecting stdout to a FILE changes python's stdout encoding (it stops being the
#     console and becomes the locale codepage). A title containing a character outside
#     that codepage would then raise UnicodeEncodeError *after* the day was already
#     written, i.e. the run looks like a failure while the artifacts are fine. Hence
#     PYTHONIOENCODING/PYTHONUTF8 below.
#
# Extra arguments are appended, so `daily_task.ps1 --help` is a safe smoke test (it
# prints the pipeline's help into that day's log and exits without touching anything).
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# Mode is the first positional word; anything else is forwarded to pipeline.py.
$mode = 'catchup'
$rest = @($args)
if ($args.Count -gt 0 -and ($args[0] -in @('noon', 'night', 'catchup', 'day'))) {
    $mode = if ($args[0] -eq 'day') { 'catchup' } else { $args[0] }
    $rest = if ($args.Count -gt 1) { @($args[1..($args.Count - 1)]) } else { @() }
}

# A missed 23:30 run is started by StartWhenAvailable as soon as the machine wakes.
# If that is the next morning, "today" is the wrong target: it would process a day that
# has barely begun and write .night_done for it, which then makes the real 23:30 run skip
# itself. Anything before the evening is therefore treated as yesterday's catch-up.
if ($mode -eq 'night' -and (Get-Date).Hour -lt 20) { $mode = 'catchup' }

$today = (Get-Date).ToString('yyyy-MM-dd')
$yesterday = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')
$targetDay = if ($mode -in @('noon', 'night')) { $today } else { $yesterday }

$stamp = Get-Date -Format 'yyyy-MM-dd'
$suffix = switch ($mode) {
    'noon' { '-noon' }
    'night' { '-night' }
    default { '' }
}
$out = Join-Path $logDir ('daily-{0}{1}.log' -f $stamp, $suffix)
$err = Join-Path $logDir ('daily-{0}{1}.err.log' -f $stamp, $suffix)

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$python = (Get-Command python.exe).Source
$script = Join-Path $PSScriptRoot 'pipeline.py'
$modeArgs = @('--date', $targetDay)
$allArgs = @($script) + $modeArgs + @('--prefetch-bodies') + $rest

# Per-pass markers make logon recovery idempotent without treating a 23:30 snapshot
# as the final word: catch-up still runs once on the next logon to collect late posts.
if ($rest.Count -eq 0) {
    $dayDir = Join-Path $root ('output\biz-daily\' + $targetDay)
    $marker = switch ($mode) {
        'noon' { '.noon_done' }
        'night' { '.night_done' }
        default { '.catchup_done' }
    }
    $done = Test-Path (Join-Path $dayDir $marker)
    if ($done) {
        $note = '===== {0}  [{1}] {2} already complete - skip =====' -f `
            (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $mode, $targetDay
        Add-Content -Path (Join-Path $logDir 'daily-task.log') -Value $note -Encoding UTF8
        exit 0
    }
}

# Start-Process OVERWRITES its redirect targets, so a same-day rerun would erase the log
# of the run that just failed - exactly the one worth keeping. Rotate any existing pair to
# daily-<date>.run<N>[.err].log first, so every attempt stays on disk. N is the first free
# index, which also works when several reruns happen in one day.
if (Test-Path $out) {
    $n = 1
    while (Test-Path (Join-Path $logDir ('daily-{0}{1}.run{2}.log' -f $stamp, $suffix, $n))) { $n++ }
    Move-Item -Path $out -Destination (Join-Path $logDir ('daily-{0}{1}.run{2}.log' -f $stamp, $suffix, $n))
    if (Test-Path $err) {
        Move-Item -Path $err -Destination (Join-Path $logDir ('daily-{0}{1}.run{2}.err.log' -f $stamp, $suffix, $n))
    }
}

# Start-Process writes the raw bytes through: no PowerShell error-record wrapping, and
# no shell quoting to get wrong.
$proc = Start-Process -FilePath $python -ArgumentList $allArgs -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err

# Fallback for the HTML stage.
#
# `pipeline.py` fetches, summarises, writes the day, then renders `index.html`. That last step
# has hung twice now (2026-10-18: 11 hours with no output; 2026-10-23: the run died after the
# README and never produced the page). Losing the page also loses the mail, because mailing
# happens only on success - so a single slow step silently costs the whole morning report.
#
# The fetch and summaries are already on disk at that point (`.articles.json`), so rendering is
# retried on its own. `$proc` is replaced by the retry's exit code, which is what lets the mail
# and index steps below run on a day where only the renderer failed.
if ($proc.ExitCode -ne 0) {
    $dayDir = Join-Path $root ('output\biz-daily\' + $targetDay)
    $art = Join-Path $dayDir '.articles.json'
    $idx = Join-Path $dayDir 'index.html'
    $htmlScript = Join-Path $PSScriptRoot 'generate_html.py'
    # Only retry when the data is there and the page is missing or older than the data:
    # a page newer than `.articles.json` means rendering already happened this run.
    $needsRender = (Test-Path $art) -and (Test-Path $htmlScript)
    if ($needsRender -and (Test-Path $idx)) {
        $needsRender = (Get-Item $art).LastWriteTime -gt (Get-Item $idx).LastWriteTime
    }
    if ($needsRender) {
        Add-Content -Path (Join-Path $logDir 'daily-task.log') -Value `
            ('===== {0}  [{1}] {2} pipeline exit {3}, retrying HTML only =====' -f `
                (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $mode, $targetDay, $proc.ExitCode) -Encoding UTF8
        $retryProc = Start-Process -FilePath $python `
            -ArgumentList @($htmlScript, '--date', $targetDay) `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput (Join-Path $logDir ('html-retry-{0}{1}.log' -f $stamp, $suffix)) `
            -RedirectStandardError (Join-Path $logDir ('html-retry-{0}{1}.err.log' -f $stamp, $suffix))
        if ($retryProc.ExitCode -eq 0) {
            $proc = $retryProc
        }
    }
}

if ($proc.ExitCode -eq 0 -and $rest.Count -eq 0) {
    $dayDir = Join-Path $root ('output\biz-daily\' + $targetDay)
    if (Test-Path $dayDir) {
        $marker = switch ($mode) {
            'noon' { '.noon_done' }
            'night' { '.night_done' }
            default { '.catchup_done' }
        }
        Set-Content -Path (Join-Path $dayDir $marker) -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -Encoding UTF8
    }

    # Retrieval indexes lag behind unless something refreshes them: on 2026-10-22 the
    # semantic index was still at 10-15 (7 days stale) and the phrase index at 10-17, so
    # `search` simply could not find anything newer. Both run here, after the day is on
    # disk, in their own logs -- a failure is recorded and never fails the day.
    $searchScript = Join-Path $PSScriptRoot 'semantic_search.py'
    if ($mode -in @('night', 'catchup') -and (Test-Path $searchScript)) {
        $idxOut = Join-Path $logDir ('index-{0}.log' -f $stamp)
        $idxErr = Join-Path $logDir ('index-{0}.err.log' -f $stamp)
        # `update` only embeds what is not in the index yet (4,061 new records on the first
        # run after a week). `phrase-build` is local FTS, no API, so it rebuilds in full.
        $idxProc = Start-Process -FilePath $python `
            -ArgumentList @($searchScript, 'update', '--article-days', '30') `
            -NoNewWindow -Wait -PassThru -RedirectStandardOutput $idxOut -RedirectStandardError $idxErr
        $phraseProc = Start-Process -FilePath $python `
            -ArgumentList @($searchScript, 'phrase-build') `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput (Join-Path $logDir ('phrase-{0}.log' -f $stamp)) `
            -RedirectStandardError (Join-Path $logDir ('phrase-{0}.err.log' -f $stamp))
        if ($idxProc.ExitCode -ne 0 -or $phraseProc.ExitCode -ne 0) {
            Add-Content -Path (Join-Path $logDir 'daily-task.log') -Value `
                ('===== {0}  index update failed (update={1}, phrase={2}), see {3} =====' -f `
                    (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $idxProc.ExitCode, $phraseProc.ExitCode, $idxErr) -Encoding UTF8
        }
    }

    # **Both passes mail now.** The noon pass used to be silent, which read as "it never
    # ran" - the only way to tell it had done anything was to open the log. The two emails
    # are clearly different: the noon one is labelled 截至中午 and the night pass adds new
    # articles since then. Mail is still its own step with its own log pair, so a mail
    # failure never fails the day nor blocks the noon marker.
    $mailScript = Join-Path $PSScriptRoot 'daily_mail.py'
    $mailOut = Join-Path $logDir ('daily-mail-{0}{1}.log' -f $stamp, $suffix)
    $mailErr = Join-Path $logDir ('daily-mail-{0}{1}.err.log' -f $stamp, $suffix)
    if (Test-Path $mailScript) {
        $mailArgs = @($mailScript, '--date', $targetDay, '--incremental')
        # **只传 ASCII**：这个脚本是无 BOM 的 UTF-8，而 Windows PowerShell 5.1 会把
        # 无 BOM 文件按 ANSI（本机 GBK）解码——直接写中文标签会变成 `鎴嚦涓崍`
        # 这种乱码进到邮件主题里（2026-10-26 实测）。中文由 Python 侧映射。
        if ($mode -eq 'noon') { $mailArgs += @('--label', 'noon') }
        $mailProc = Start-Process -FilePath $python -ArgumentList $mailArgs `
            -NoNewWindow -Wait -PassThru -RedirectStandardOutput $mailOut -RedirectStandardError $mailErr
        if ($mailProc.ExitCode -ne 0) {
            Add-Content -Path (Join-Path $logDir 'daily-task.log') -Value `
                ('===== {0}  [{1}] {2} mail failed (exit {3}), see {4} =====' -f `
                    (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $mode, $targetDay, $mailProc.ExitCode, $mailErr) -Encoding UTF8
        }
    }
}

exit $proc.ExitCode
