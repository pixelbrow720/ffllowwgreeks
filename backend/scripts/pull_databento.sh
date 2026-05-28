#!/usr/bin/env bash
# Resumable Databento historical pull — Plan C (3 months SPX/NDX/ES/NQ).
#
# Discipline:
#   - Sequential, no parallel. No retry.
#   - Skip files that already exist with non-zero size.
#   - On HTTP != 200, log verbose error, exit 1 immediately.
#   - Append per-call cost to ledger so total is auditable.
#
# Usage:
#   bash scripts/pull_databento.sh smoke      # one cheap call (NDX definition, ~$0.02)
#   bash scripts/pull_databento.sh full       # all trading days × schemas

set -u
shopt -s nullglob

ROOT="/c/FLOWGREEKS/backend"
DATA="$ROOT/data/databento"
LEDGER="$DATA/_ledger.csv"
LOG="$DATA/_pull.log"
HOST="https://hist.databento.com"

mkdir -p "$DATA"

# Load API key
if [ ! -f "$ROOT/.env" ]; then
  echo "FATAL: $ROOT/.env not found"; exit 1
fi
KEY=$(grep '^DATABENTO_API_KEY=' "$ROOT/.env" | cut -d'=' -f2- | tr -d '\r\n"'"'"' ')
if [ -z "$KEY" ]; then
  echo "FATAL: DATABENTO_API_KEY empty in .env"; exit 1
fi
if [ ${#KEY} -lt 20 ]; then
  echo "FATAL: DATABENTO_API_KEY looks malformed (len=${#KEY})"; exit 1
fi

# Trading day list (US RTH, Feb–May 2026, ex weekends + holidays)
# Holidays in window: 2026-02-16 (Presidents'), 2026-04-03 (Good Friday)
TRADING_DAYS=(
  2026-02-02 2026-02-03 2026-02-04 2026-02-05 2026-02-06
  2026-02-09 2026-02-10 2026-02-11 2026-02-12 2026-02-13
  2026-02-17 2026-02-18 2026-02-19 2026-02-20
  2026-02-23 2026-02-24 2026-02-25 2026-02-26 2026-02-27
  2026-03-02 2026-03-03 2026-03-04 2026-03-05 2026-03-06
  2026-03-09 2026-03-10 2026-03-11 2026-03-12 2026-03-13
  2026-03-16 2026-03-17 2026-03-18 2026-03-19 2026-03-20
  2026-03-23 2026-03-24 2026-03-25 2026-03-26 2026-03-27
  2026-03-30 2026-03-31 2026-04-01 2026-04-02
  2026-04-06 2026-04-07 2026-04-08 2026-04-09 2026-04-10
  2026-04-13 2026-04-14 2026-04-15 2026-04-16 2026-04-17
  2026-04-20 2026-04-21 2026-04-22 2026-04-23 2026-04-24
  2026-04-27 2026-04-28 2026-04-29 2026-04-30
  2026-05-01
  2026-05-04 2026-05-05 2026-05-06 2026-05-07 2026-05-08
  2026-05-11 2026-05-12 2026-05-13 2026-05-14 2026-05-15
  2026-05-18 2026-05-19 2026-05-20 2026-05-21 2026-05-22
)

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"
}

# Bundles to pull per trading day:
#   format: dataset|schema|symbols|stype_in|window
# window = "rth" (13:30-20:15Z) or "day" (00:00-24:00Z)
BUNDLES=(
  "OPRA.PILLAR|definition|SPX.OPT,SPXW.OPT|parent|day"
  "OPRA.PILLAR|definition|NDX.OPT,NDXP.OPT|parent|day"
  "OPRA.PILLAR|statistics|SPX.OPT,SPXW.OPT|parent|day"
  "OPRA.PILLAR|statistics|NDX.OPT,NDXP.OPT|parent|day"
  "OPRA.PILLAR|tcbbo|SPX.OPT,SPXW.OPT|parent|rth"
  "OPRA.PILLAR|tcbbo|NDX.OPT,NDXP.OPT|parent|rth"
  "GLBX.MDP3|mbp-1|ES.FUT|parent|rth"
  "GLBX.MDP3|mbp-1|NQ.FUT|parent|rth"
  "GLBX.MDP3|trades|ES.FUT|parent|rth"
  "GLBX.MDP3|trades|NQ.FUT|parent|rth"
)

window_start() { local d=$1 w=$2; if [ "$w" = "rth" ]; then echo "${d}T13:30:00Z"; else echo "${d}T00:00:00Z"; fi; }
window_end()   { local d=$1 w=$2; if [ "$w" = "rth" ]; then echo "${d}T20:15:00Z"; else local n=$(date -u -d "$d +1 day" +%Y-%m-%d 2>/dev/null || date -u -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d); echo "${n}T00:00:00Z"; fi; }

slug_symbols() { echo "$1" | tr ',' '_' | tr -d ':' | tr '.' '-'; }

target_path() {
  local day=$1 dataset=$2 schema=$3 symbols=$4
  local sym_slug
  sym_slug=$(slug_symbols "$symbols")
  echo "$DATA/$day/${dataset//./_}/${schema}__${sym_slug}.dbn.zst"
}

ensure_ledger() {
  if [ ! -f "$LEDGER" ]; then
    echo "ts,day,dataset,schema,symbols,window,status,bytes,cost_usd,target" > "$LEDGER"
  fi
}

cost_estimate() {
  # Returns USD as a decimal string. Best-effort; one short HTTP call.
  local dataset=$1 schema=$2 symbols=$3 stype_in=$4 start=$5 end=$6
  curl -s -u "$KEY:" "$HOST/v0/metadata.get_cost" -G \
    --data-urlencode "dataset=$dataset" \
    --data-urlencode "schema=$schema" \
    --data-urlencode "symbols=$symbols" \
    --data-urlencode "stype_in=$stype_in" \
    --data-urlencode "start=$start" \
    --data-urlencode "end=$end" \
    --data-urlencode "mode=historical-streaming" \
    --max-time 60
}

pull_bundle() {
  local day=$1 bundle=$2
  IFS='|' read -r dataset schema symbols stype_in window <<< "$bundle"
  local start end target tmp http
  start=$(window_start "$day" "$window")
  end=$(window_end "$day" "$window")
  target=$(target_path "$day" "$dataset" "$schema" "$symbols")
  mkdir -p "$(dirname "$target")"

  # Resume: skip if non-empty file already exists
  if [ -s "$target" ]; then
    log "SKIP $day $dataset $schema $symbols (already $(stat -c%s "$target" 2>/dev/null) bytes)"
    return 0
  fi

  # Cost estimate (best effort; 504 here is non-fatal — we proceed and let real call decide)
  local est
  est=$(cost_estimate "$dataset" "$schema" "$symbols" "$stype_in" "$start" "$end")
  if ! [[ "$est" =~ ^[0-9.]+$ ]]; then
    est="?"
  fi
  log "PULL $day $dataset $schema $symbols  est=\$$est  → $target"

  tmp="${target}.partial"
  rm -f "$tmp"

  # The actual download. zstd encoding default for binary efficiency.
  http=$(curl -s -u "$KEY:" -o "$tmp" -w "%{http_code}" \
    "$HOST/v0/timeseries.get_range" \
    -G \
    --data-urlencode "dataset=$dataset" \
    --data-urlencode "schema=$schema" \
    --data-urlencode "symbols=$symbols" \
    --data-urlencode "stype_in=$stype_in" \
    --data-urlencode "start=$start" \
    --data-urlencode "end=$end" \
    --data-urlencode "encoding=dbn" \
    --data-urlencode "compression=zstd" \
    --max-time 1800)

  # Retry policy for transient errors:
  #   - 504: Databento server-side gateway timeout (large files trigger this)
  #   - 000: curl could not connect (network blip on our side, DNS, etc)
  # Two retries total with escalating sleep — gives the server enough time
  # to recover under load. No third retry to avoid hammering anything.
  for attempt in 1 2; do
    if [ "$http" = "504" ] || [ "$http" = "000" ]; then
      local sleep_s=90
      [ "$attempt" = "2" ] && sleep_s=240
      log "RETRY ($attempt/2) HTTP=$http $day $dataset $schema $symbols  — sleeping ${sleep_s}s"
      rm -f "$tmp"
      sleep "$sleep_s"
      http=$(curl -s -u "$KEY:" -o "$tmp" -w "%{http_code}" \
        "$HOST/v0/timeseries.get_range" \
        -G \
        --data-urlencode "dataset=$dataset" \
        --data-urlencode "schema=$schema" \
        --data-urlencode "symbols=$symbols" \
        --data-urlencode "stype_in=$stype_in" \
        --data-urlencode "start=$start" \
        --data-urlencode "end=$end" \
        --data-urlencode "encoding=dbn" \
        --data-urlencode "compression=zstd" \
        --max-time 1800)
    else
      break
    fi
  done

  if [ "$http" != "200" ]; then
    log "FAIL HTTP=$http $day $dataset $schema $symbols"
    log "FAIL body: $(head -c 600 "$tmp" 2>/dev/null)"
    rm -f "$tmp"
    ensure_ledger
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),$day,$dataset,$schema,$symbols,$window,FAIL_$http,0,0,$target" >> "$LEDGER"
    return 1
  fi

  local bytes
  bytes=$(stat -c%s "$tmp" 2>/dev/null || echo 0)
  if [ "$bytes" -lt 100 ]; then
    log "FAIL tiny response (bytes=$bytes) $day $dataset $schema $symbols"
    log "FAIL body: $(head -c 600 "$tmp" 2>/dev/null)"
    rm -f "$tmp"
    ensure_ledger
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),$day,$dataset,$schema,$symbols,$window,FAIL_TINY,$bytes,0,$target" >> "$LEDGER"
    return 1
  fi

  mv "$tmp" "$target"
  ensure_ledger
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),$day,$dataset,$schema,$symbols,$window,OK,$bytes,$est,$target" >> "$LEDGER"
  log "OK   $day $dataset $schema $symbols  bytes=$bytes  est=\$$est"
  return 0
}

mode="${1:-help}"

case "$mode" in
  smoke)
    log "=== SMOKE: NDX definition for ${TRADING_DAYS[0]} ==="
    pull_bundle "${TRADING_DAYS[0]}" "OPRA.PILLAR|definition|NDX.OPT,NDXP.OPT|parent|day" || exit 1
    log "=== SMOKE OK ==="
    ;;
  full)
    log "=== FULL PULL START — ${#TRADING_DAYS[@]} days × ${#BUNDLES[@]} bundles ==="
    total_calls=$((${#TRADING_DAYS[@]} * ${#BUNDLES[@]}))
    done_calls=0
    for day in "${TRADING_DAYS[@]}"; do
      for bundle in "${BUNDLES[@]}"; do
        done_calls=$((done_calls + 1))
        log ">>> [$done_calls/$total_calls] day=$day"
        if ! pull_bundle "$day" "$bundle"; then
          log "=== STOP on first error. Re-run script to resume — completed files are skipped. ==="
          exit 1
        fi
      done
    done
    log "=== FULL PULL COMPLETE ==="
    ;;
  *)
    echo "usage: $0 {smoke|full}"
    exit 2
    ;;
esac
