"""Turn ``mutmut results`` into docs/mutation.json (the numbers come only from the run)."""

from __future__ import annotations

import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

results, out, children = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
counts: Counter[str] = Counter()
survivors: list[str] = []
for line in results.read_text(encoding="utf-8").splitlines():
    # "    schemashift.equivalence.rules.foo.x_bar__mutmut_3: survived"
    m = re.match(r"\s*(\S+):\s*(\w[\w ]*)$", line)
    if not m:
        continue
    name, status = m.group(1), m.group(2).strip()
    counts[status] += 1
    if status in ("survived", "no tests"):
        survivors.append(name)
killed = counts["killed"]
total = sum(counts.values())
score = killed / total if total else None
out.write_text(
    json.dumps(
        {
            "date": datetime.date.today().isoformat(),
            "tool": "mutmut 3",
            "command": f"scripts/mutation.sh {children}",
            "paths": ["schemashift/equivalence/", "schemashift/optimizer/"],
            "tests": ["tests/unit/equivalence/", "tests/unit/optimizer/", "tests/unit/metrics/test_corpus.py"],
            "total": total,
            "killed": killed,
            "survived": counts["survived"],
            "no_tests": counts["no tests"],
            "timeout": counts["timeout"],
            "suspicious": counts["suspicious"],
            "skipped": counts["skipped"],
            "score": score,
            "survivors": survivors,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print(f"mutation score {score:.1%} ({killed}/{total})" if score is not None else "no mutants")
