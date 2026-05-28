#!/usr/bin/env bash
# Plan B: 88-trading-day wide-range Databento pull.
# 10 calls total — ONE per (dataset, schema, symbols).
# Sorted ascending by expected cost. STOP on first non-200 response.
#
# Usage: bash scripts/pull_wide.sh
# Resume: just re-run; existing files are skipped.

set -u

ROOT="/c/FLOWGREEKS/backend"
DATA="$ROOT/data/databento/_wide"
LOG="$DATA/_wide.log"
HOST="https://hist.databento.com"
START="2026-01-13T00:00:00Z"
END="2026-05-23T00:00:00Z"

mkdir -p "$DATA"

KEY=$(grep '^DATABENTO_API_KEY=' "$ROOT/.env" | cut -d'=' -f2- | tr -d '\r\n"'"'"' ')
if [ ${#KEY} -lt 20 ]; then
  echo "FATAL: DATABENTO_API_KEY missing or malformed" | tee -a "$LOG"
  exit 1
fi

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

call() {
  local idx="$1" est="$2" ds="$3" schema="$4" symbols="$5" stype_in="$6"
  local sym_slug
  sym_slug=$(echo "$symbols" | tr ',' '_' | tr ':.' '-')
  local tgt="$DATA/${idx}__${ds//./_}__${schema}__${sym_slug}.dbn.zst"

  if [ -s "$tgt" ]; then
    log "SKIP [$idx/10] $ds $schema $symbols (already $(stat -c%s "$tgt") bytes)"
    return 0
  fi

  log "PULL [$idx/10] $ds $schema $symbols  est=\$$est"
  local tmp="${tgt}.partial"
  rm -f "$tmp"

  local http
  http=$(curl -s -u "$KEY:" -o "$tmp" -w "%{http_code}" \
    "$HOST/v0/timeseries.get_range" -G \
    --data-urlencode "dataset=$ds" \
    --data-urlencode "schema=$schema" \
    --data-urlencode "symbols=$symbols" \
    --data-urlencode "stype_in=$stype_in" \
    --data-urlencode "start=$START" \
    --data-urlencode "end=$END" \
    --data-urlencode "encoding=dbn" \
    --data-urlencode "compression=zstd" \
    --max-time 7200)

  if [ "$http" != "200" ]; then
    log "FAIL HTTP=$http [$idx/10] $ds $schema $symbols"
    log "FAIL body: $(head -c 600 "$tmp" 2>/dev/null)"
    rm -f "$tmp"
    return 1
  fi

  local bytes
  bytes=$(stat -c%s "$tmp" 2>/dev/null || echo 0)
  if [ "$bytes" -lt 1000 ]; then
    log "FAIL tiny response (bytes=$bytes) [$idx/10] $ds $schema $symbols"
    log "FAIL body: $(head -c 600 "$tmp" 2>/dev/null)"
    rm -f "$tmp"
    return 1
  fi

  mv "$tmp" "$tgt"
  local mb
  mb=$(awk "BEGIN { printf \"%.1f\", $bytes/1048576 }")
  log "  OK [$idx/10] bytes=$bytes (${mb} MB)"
  return 0
}

log "=== PLAN B WIDE PULL START — window $START to $END ==="

# Sorted ascending by expected cost (cheapest first):
call 01    3 OPRA.PILLAR definition NDX.OPT,NDXP.OPT  parent || exit 1
call 02    4 OPRA.PILLAR definition SPX.OPT,SPXW.OPT  parent || exit 1
call 03   17 OPRA.PILLAR statistics NDX.OPT,NDXP.OPT  parent || exit 1
call 04   28 OPRA.PILLAR statistics SPX.OPT,SPXW.OPT  parent || exit 1
call 05   40 GLBX.MDP3   trades     NQ.FUT            parent || exit 1
call 06   50 GLBX.MDP3   trades     ES.FUT            parent || exit 1
call 07   70 OPRA.PILLAR tcbbo      NDX.OPT,NDXP.OPT  parent || exit 1
call 08  130 GLBX.MDP3   mbp-1      ES.FUT            parent || exit 1
call 09  170 GLBX.MDP3   mbp-1      NQ.FUT            parent || exit 1
call 10 1800 OPRA.PILLAR tcbbo      SPX.OPT,SPXW.OPT  parent || exit 1

log "=== ALL 10 CALLS COMPLETE ==="
