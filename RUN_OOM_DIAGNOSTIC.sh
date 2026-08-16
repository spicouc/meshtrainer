#!/usr/bin/env bash
# R1.2 DIAGNÒSTIC OOM — captura mètriques abans/durant/després del distributed
set -u
OUT="/root/meshtrainer/evidence/minicpm5/OOM_DIAGNOSTIC.log"
mkdir -p "$(dirname "$OUT")"
LOG="tee -a $OUT"

echo "════════════════════════════════════════════" | $LOG
echo "  OOM DIAGNOSTIC — $(date -u +%Y-%m-%dT%H:%M:%SZ)" | $LOG
echo "════════════════════════════════════════════" | $LOG

snap() {
    echo "" | $LOG
    echo "─── SNAPSHOT $1 ($(date -u +%H:%M:%S)) ───" | $LOG
    echo "[free -h]" | $LOG; free -h | $LOG
    echo "[swapon --show]" | $LOG; swapon --show | $LOG
    echo "[/proc/meminfo (clau)]" | $LOG
    grep -E "MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|Active\(anon\)|Inactive\(anon\)|Active\(file\)|Inactive\(file\)|Unevictable|Mlocked|Dirty|Writeback|AnonPages|Mapped|Shmem|Slab|SReclaimable|SUnreclaim|KernelStack|PageTables|CommitLimit|Committed_AS" /proc/meminfo | $LOG
    echo "[vm params]" | $LOG
    for p in vm.swappiness vm.overcommit_memory vm.overcommit_ratio vm.min_free_kbytes vm.vfs_cache_pressure; do
        echo "  $p = $(cat /proc/sys/$(echo $p | tr '.' '/'))" | $LOG
    done
    echo "[oom_score dels processos rellevants]" | $LOG
    for pid in $(ps -eo pid,comm | grep -E "python|kvm|qemu|hermes" | awk '{print $1}' | head -20); do
        if [ -r /proc/$pid/oom_score ] && [ -r /proc/$pid/oom_score_adj ]; then
            sc=$(cat /proc/$pid/oom_score 2>/dev/null)
            adj=$(cat /proc/$pid/oom_score_adj 2>/dev/null)
            rss=$(awk '/VmRSS/{print $2}' /proc/$pid/status 2>/dev/null)
            cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-60)
            echo "  pid=$pid rss=${rss}kB score=$sc adj=$adj $cmd" | $LOG
        fi
    done
    echo "[cgroup CT112 (v2, des del host)]" | $LOG
    CG="/sys/fs/cgroup/lxc/112"
    for f in memory.current memory.max memory.swap.current memory.swap.max memory.peak memory.events; do
        v=$(cat $CG/$f 2>/dev/null)
        echo "  $f = $v" | $LOG
    done
}

snap "ABANS (host, estat base)"

# execució del distributed en foreground (captura stdout)
echo "" | $LOG
echo "─── EXECUCIÓ DISTRIBUTED (R1.2 overlap real) ───" | $LOG
echo "+800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python generic_distributed_run.py --backend minicpm5 --model /root/minicpm5_1b_snapshot --num-examples 6 --seq-len 64 --out-dir /root/meshtrainer/minicpm5_output" | $LOG

# monitor en background: snapshot cada 10s
(
    for i in $(seq 1 120); do
        sleep 10
        snap "t+${i}0s (durant)"
    done
) &
MON_PID=$!

lxc-attach -n 112 -- bash -c 'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && rm -rf minicpm5_output && mkdir -p minicpm5_output && /opt/qwen3-venv/bin/python generic_distributed_run.py --backend minicpm5 --model /root/minicpm5_1b_snapshot --num-examples 6 --seq-len 64 --out-dir /root/meshtrainer/minicpm5_output' > /tmp/mc_diag.log 2>&1
RC=$?
kill $MON_PID 2>/dev/null
echo "DISTRIBUTED_EXIT=$RC" | $LOG
echo "=== distributed.log (darrer fragment) ===" | $LOG
tail -25 /tmp/mc_diag.log | $LOG

snap "DESPRÉS (host, estat final)"

# journal del kernel al voltant de l'OOM
echo "" | $LOG
echo "─── JOURNAL KERNEL (OOM) ───" | $LOG
journalctl -k --since "25 minutes ago" 2>/dev/null | grep -B30 -A15 -iE "oom-kill|out of memory|oom_reaper" | tail -200 | $LOG

echo "" | $LOG
echo "═══ FI DIAGNÒSTIC (exit=$RC) ═══" | $LOG
