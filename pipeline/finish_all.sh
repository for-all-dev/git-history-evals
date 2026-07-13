#!/usr/bin/env bash
# Detached driver: finish the whole-proof validation end-to-end.
#  1. wait for any in-flight builds/validations
#  2. rebuild hex-dev's modules with a cc shim (-D_GNU_SOURCE; its FFI needs it)
#  3. re-validate every repo whose build environment changed
#  4. re-scan all dry results for environmental contamination
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; cd "$ROOT"
log="$S/finish_all.log"; : > "$log"
say() { echo "[$(date +%H:%M:%S)] $*" >> "$log"; }

say "waiting for in-flight builds/validation"
while pgrep -f 'build_modules_tolerant.sh|validate_whole.sh' > /dev/null; do sleep 60; done

say "rebuilding hex-dev modules with cc shim"
printf 'hex-dev\n' > "$S/hexdev.txt"
PATH="$S/ccshim:$PATH" bash pipeline/build_modules_tolerant.sh "$S" "$S/hexdev.txt" >> "$log" 2>&1

say "re-validating repos whose environment changed"
# everything that was contaminated, plus CvxLean (wrongly skipped by the old probe gate)
python3 -c "
import json
xs=set(json.load(open('$S/contaminated.json')))|set(json.load(open('$S/contaminated2.json')))|{'CvxLean'}
open('$S/final_revalidate.txt','w').write('\n'.join(sorted(xs))+'\n')
"
while read -r n; do [ -n "$n" ] && rm -f "$S/dry/$n.jsonl" "$S/dry/$n.good.jsonl"; done < "$S/final_revalidate.txt"
say "re-validating: $(tr '\n' ' ' < "$S/final_revalidate.txt")"
: > "$S/validate_whole.log"
PATH="$S/ccshim:$PATH" bash pipeline/validate_whole.sh "$S" >> "$log" 2>&1

say "re-scanning for environmental contamination"
python3 - <<'PY' >> "$log" 2>&1
import json,glob,os,re
S=os.environ.get('SCR')
ENV=re.compile(r"unknown module prefix|unknown package|could not resolve 'HEAD'|cloning|compiled configuration is invalid|exited with code 128|object file .* does not exist",re.I)
out={}
for f in sorted(glob.glob(f"{S}/dry/*.jsonl")):
    n=os.path.basename(f)[:-6]
    if n.endswith('.good'): continue
    tot=env=good=0
    for l in open(f):
        r=json.loads(l); tot+=1
        if r.get('dry_run') and r.get('solution_compiles') is True: good+=1
        if ENV.search(r.get('error') or ''): env+=1
    out[n]={'total':tot,'good':good,'env':env}
json.dump(out, open(f"{S}/final_scan.json","w"), indent=1)
print("STILL CONTAMINATED:", {k:v for k,v in out.items() if v['env']})
PY
say "FINISH_ALL DONE"
