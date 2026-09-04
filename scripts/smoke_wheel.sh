#!/bin/sh
# Prove that what we would publish is what we tested: build the wheel, install
# it into a clean venv, and run the documented entry point from OUTSIDE the
# checkout.
#
# The last part is the point. A smoke test run from the repository root can
# pass on the working tree instead of the installed package -- `src/` on the
# path, a stale editable install, a `tracelink` shadowed by the checkout --
# and report a green wheel that nobody actually executed. So the venv, the
# fixtures and the working directory all live in a scratch directory, and an
# explicit sentinel refuses to continue unless `tracelink.__file__` resolves
# inside that venv.
#
#   scripts/smoke_wheel.sh [python]
#
# Exits non-zero, loudly, on the first thing that is not true.
set -eu

PYTHON="${1:-python3}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A PYTHONPATH inherited from the caller would defeat the whole exercise.
unset PYTHONPATH

echo "== a clean venv, which also does the building =="
# The venv's own pip builds the wheel: the caller's interpreter may have a
# distro pip that cannot even import, and a wheel built by the environment
# that will install it is one fewer difference between here and CI.
"$PYTHON" -m venv "$WORK/venv"
VENV_PY="$WORK/venv/bin/python"
VENV_TL="$WORK/venv/bin/tracelink"

echo "== building the wheel =="
# Into the scratch directory, not the checkout: this script must leave the
# working tree exactly as it found it.
"$VENV_PY" -m pip wheel "$REPO" --no-deps --wheel-dir "$WORK/dist" --quiet
WHEEL="$(ls "$WORK"/dist/*.whl)"
echo "built $(basename "$WHEEL")"

echo "== installing it =="
"$VENV_PY" -m pip install --quiet --no-deps "$WHEEL"

echo "== the package under test is the installed one =="
cd "$WORK"          # never run any of the following from the checkout
"$VENV_PY" - "$WORK/venv" <<'PY'
import sys, os, tracelink
venv = os.path.realpath(sys.argv[1])
where = os.path.realpath(tracelink.__file__)
print(where)
if not where.startswith(venv + os.sep):
    sys.exit(f"tracelink was imported from {where}, not from the venv {venv}")
PY

echo "== the documented entry point exists and reports its version =="
VERSION="$("$VENV_TL" --version)"
EXPECTED="$("$PYTHON" "$REPO/scripts/bump.py" --print)"
test "$VERSION" = "$EXPECTED" \
  || { echo "tracelink --version said $VERSION, package is $EXPECTED"; exit 1; }
echo "tracelink --version = $VERSION"

echo "== sub-commands keep their own name in usage =="
"$VENV_TL" lint --help | head -1 | grep -q '^usage: tracelink lint' \
  || { echo "lint --help did not say 'usage: tracelink lint'"; exit 1; }

echo "== end to end, on the installed package, outside the checkout =="
cp "$REPO/examples/FINDINGS.example.md" "$WORK/FINDINGS.md"
cp -r "$REPO/examples/demo-project" "$WORK/project"
cd "$WORK/project"
"$VENV_TL" split --register "$WORK/FINDINGS.md" --out "$WORK/vault" --prefix RES
"$VENV_TL" index --repo . --backend scan --out "$WORK/symbols.json"
"$VENV_TL" link --vault "$WORK/vault" --symbols "$WORK/symbols.json" --repo .
"$VENV_TL" link --vault "$WORK/vault" --symbols "$WORK/symbols.json" --repo . --check
"$VENV_TL" status --register "$WORK/FINDINGS.md" --vault "$WORK/vault" \
  --symbols "$WORK/symbols.json" --repo . >/dev/null

echo "== a project can be started and diagnosed from the wheel =="
mkdir -p "$WORK/fresh/src"
printf 'def compute_total():\n    return 1\n' > "$WORK/fresh/src/a.py"
(cd "$WORK/fresh" && "$VENV_TL" init >/dev/null && "$VENV_TL" sync >/dev/null \
   && "$VENV_TL" doctor >/dev/null) \
  || { echo "init -> sync -> doctor failed on a fresh project"; exit 1; }
(cd "$WORK/fresh" && "$VENV_TL" sync --check) \
  || { echo "a sync straight after a sync reported work to do"; exit 1; }

echo "== the public primitive answers, in text and in json =="
"$VENV_TL" consult src/parser.py --repo . --vault "$WORK/vault" \
  | grep -q 'known findings about this file' \
  || { echo "consult said nothing about a linked file"; exit 1; }
"$VENV_TL" consult parse_payload --repo . --vault "$WORK/vault" --json \
  > "$WORK/consult.json"
"$VENV_PY" - "$WORK/consult.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))          # the whole of stdout, or it fails
assert doc["schema_version"] == 1, doc
assert doc["target"]["kind"] == "symbol", doc
assert doc["hits"] and doc["hits"][0]["finding_id"], doc
print("consult --json schema", doc["schema_version"], "ok")
PY

echo "== a link can explain itself =="
"$VENV_TL" explain RES-02 --repo . --vault "$WORK/vault" \
  | grep -q 'Method:' \
  || { echo "explain did not report a method"; exit 1; }
"$VENV_TL" explain RES-02 --repo . --vault "$WORK/vault" --json \
  > "$WORK/explain.json"
"$VENV_PY" - "$WORK/explain.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["schema_version"] == 1, doc
link = doc["links"][0]
assert link["state"] == "match", link
assert link["method"], link
assert link["basis"], link
assert "internal_reason" not in link, "internal reason leaked without --debug"
print("explain --json state", link["state"], "method", link["method"], "ok")
PY

echo "== the vault the installed package produced is the real thing =="
test -s "$WORK/vault/CODE-INDEX.md" || { echo "no CODE-INDEX.md"; exit 1; }
grep -q 'RES-01' "$WORK/vault/CODE-INDEX.md" \
  || { echo "CODE-INDEX.md does not mention the findings"; exit 1; }

echo "smoke: OK"
