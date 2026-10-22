#!/usr/bin/env bash
# Mutation testing of the equivalence checker and the optimizer (mutmut does not run on Windows, so
# this uses a Linux container).
#
#   scripts/mutation.sh [max-children] [cpus]    # writes docs/mutation.json
#
# The repository (minus .venv, node_modules, .git) is streamed into the container as a tarball, so
# nothing is bind-mounted and the host is untouched. The container is limited to `cpus` CPUs and 6 GB.
# Score = killed / (killed + survived + timeout + suspicious + no tests): a mutant that no test covers
# counts as a survivor, so uncovered code cannot flatter the number.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHILDREN="${1:-6}"
CPUS="${2:-8}"
NAME="schemashift-mutation"
cd "$ROOT"
export MSYS_NO_PATHCONV=1
docker rm -f "$NAME" >/dev/null 2>&1 || true
tar --exclude=.venv --exclude=node_modules --exclude=.git --exclude=__pycache__ --exclude=dist \
    --exclude='*.png' --exclude=test-results -cf - backend samples docs scripts \
  | docker run -i --name "$NAME" --cpus "$CPUS" --memory 6g python:3.12-slim bash -c "
      set -e
      mkdir /work && tar -xf - -C /work && cd /work/backend
      pip install -q -e '.[dev]' mutmut 2>&1 | tail -1
      rm -rf mutants
      mutmut run --max-children $CHILDREN || true
      mutmut results --all true > /tmp/results.txt || true
      python /work/scripts/mutation_summary.py /tmp/results.txt /tmp/mutation.json '$CHILDREN'
      echo '---MUTATION-JSON---'; cat /tmp/mutation.json; echo '---END---'
    " | tee /tmp/mutation.out
sed -n '/---MUTATION-JSON---/,/---END---/p' /tmp/mutation.out | sed '1d;$d' > "$ROOT/docs/mutation.json"
docker rm -f "$NAME" >/dev/null 2>&1 || true
