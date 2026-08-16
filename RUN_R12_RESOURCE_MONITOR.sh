#!/usr/bin/env bash
# R12_RESOURCE_RUN.log — monitor de recursos durant el distributed R1.2
set -u
OUT="/root/meshtrainer/evidence/minicpm5/R12_RESOURCE_RUN.log"
mkdir -p "$(dirname "$OUT")"
echo "═══ R1.2 RESOURCE RUN — $(date -u +%Y-%m-%dT%H:%M:%SZ) ═══" > "$OUT"
PEAK_RAM=0; PEAK_SWAP=0; PEAK_CT=0; PEAK_CTSWAP=0

for i in $(seq 1 200); do
    sleep 5
    NOW=$(date -u +%H:%M:%S)
    read -r RAM_USED RAM_FREE <<< "$(free -m | awk 'NR==2 {print $3, $4}')"
    read -r SWAP_USED SWAP_FREE <<< "$(free -m | awk 'NR==3 {print $3, $4}')"
    CT=$(cat /sys/fs/cgroup/lxc/112/memory.current 2>/dev/null)
    CTSWAP=$(cat /sys/fs/cgroup/lxc/112/memory.swap.current 2>/dev/null)
    CTMAX=$(cat /sys/fs/cgroup/lxc/112/memory.max 2>/dev/null)
    CTSWAPMAX=$(cat /sys/fs/cgroup/lxc/112/memory.swap.max 2>/dev/null)
    [ "$CT" -gt "$PEAK_CT" ] && PEAK_CT=$CT
    [ "$CTSWAP" -gt "$PEAK_CTSWAP" ] && PEAK_CTSWAP=$CTSWAP
    [ "$RAM_USED" -gt "$PEAK_RAM" ] && PEAK_RAM=$RAM_USED
    [ "$SWAP_USED" -gt "$PEAK_SWAP" ] && PEAK_SWAP=$SWAP_USED
    # RSS dels workers (els 2 més grans)
    RSS=$(ps -eo rss,comm,args --sort=-rss | grep -E "model_worker.py" | head -2 | awk '{printf "%dMB(%s) ", $1/1024, $2}' | head -c 120)
    printf "%s | RAM %d/%dMB SWAP %d/%dMB | CT %d/%d CTSWAP %d/%d | RSS %s\n" \
        "$NOW" "$RAM_USED" "$RAM_FREE" "$SWAP_USED" "$SWAP_FREE" \
        "$CT" "$CTMAX" "$CTSWAP" "$CTSWAPMAX" "$RSS" >> "$OUT"
done
echo "" >> "$OUT"
echo "═══ PEAKS ═══" >> "$OUT"
echo "peak RAM host: ${PEAK_RAM} MB" >> "$OUT"
echo "peak swap host: ${PEAK_SWAP} MB" >> "$OUT"
echo "peak CT memory.current: ${PEAK_CT} B ($((PEAK_CT/1048576)) MB)" >> "$OUT"
echo "peak CT memory.swap.current: ${PEAK_CTSWAP} B ($((PEAK_CTSWAP/1048576)) MB)" >> "$OUT"
