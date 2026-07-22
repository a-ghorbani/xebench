#!/usr/bin/env bash
# Launch the xebench app on a device, let it auto-run ALL engines, and capture
# every XEBENCH_RESULT line from logcat (release Hermes forwards console.log to
# the ReactNativeJS tag). No tapping — the app auto-runs on launch and emits
# XEBENCH_DONE when finished.
#
# Usage: scripts/capture_app_run.sh <serial> [timeout_sec]
# Writes results/raw-<serial>-<engine>-<backend>-<modelslug>.jsonl per engine.
set -uo pipefail
SER="${1:?serial required}"; TIMEOUT="${2:-900}"
cd "$(dirname "$0")/.."
mkdir -p results

# device facts the app can't read from RN
SOC=$(adb -s "$SER" shell getprop ro.soc.model | tr -d '\r'); [ -z "$SOC" ] && SOC=$(adb -s "$SER" shell getprop ro.board.platform | tr -d '\r')
MFR=$(adb -s "$SER" shell getprop ro.product.manufacturer | tr -d '\r')
DEV=$(adb -s "$SER" shell getprop ro.product.device | tr -d '\r')
MODEL=$(adb -s "$SER" shell getprop ro.product.model | tr -d '\r')

echo "[$SER] $MODEL ($SOC): force-stop + launch (auto-run all engines)"
adb -s "$SER" shell am force-stop com.xebenchapp 2>/dev/null
adb -s "$SER" logcat -c 2>/dev/null || true
adb -s "$SER" shell input keyevent KEYCODE_WAKEUP 2>/dev/null || true
adb -s "$SER" shell am start -n com.xebenchapp/.MainActivity >/dev/null 2>&1

echo "[$SER] waiting up to ${TIMEOUT}s for XEBENCH_DONE…"
END=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$END" ]; do
  if adb -s "$SER" logcat -d 2>/dev/null | grep -qa "XEBENCH_DONE"; then break; fi
  sleep 5
done

DUMP=$(adb -s "$SER" logcat -d 2>/dev/null)
echo "$DUMP" | grep -a "XEBENCH_ERROR" | sed -E 's/.*XEBENCH_ERROR/  ERROR:/' | sort -u

# one line per engine result
COUNT=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  JSON=$(echo "$line" | sed -E 's/.*XEBENCH_RESULT //')
  OUT=$(echo "$JSON" | SOC="$SOC" MFR="$MFR" DEV="$DEV" MODEL="$MODEL" SER="$SER" python3 -c "
import json,sys,os,re
try: r=json.load(sys.stdin)
except Exception: sys.exit(0)
di=r.setdefault('deviceInfo',{})
di['soc']=di.get('soc') or os.environ['SOC']
di['manufacturer']=os.environ['MFR']; di['device']=os.environ['DEV']
di['model']=di.get('model') or os.environ['MODEL']
slug=re.sub(r'[^a-z0-9]+','',r.get('model','').lower())
path=f\"results/raw-{os.environ['SER']}-{r['engine'].replace('.','')}-{r.get('backend','cpu')}-{slug}.jsonl\"
json.dump(r,open(path,'w'))
s=r['summary']
print(f\"{path}|{r['engine']}/{r.get('backend')} {r['model']} {r['quant']}: pp={s['prefillTps']['median']} tg={s['decodeTps']['median']}\")
")
  [ -n "$OUT" ] && { echo "  ${OUT#*|}"; COUNT=$((COUNT+1)); }
done < <(echo "$DUMP" | grep -a "XEBENCH_RESULT")

echo "[$SER] captured $COUNT engine result(s)"
[ "$COUNT" -eq 0 ] && exit 2 || exit 0
