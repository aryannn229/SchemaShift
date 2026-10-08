from __future__ import annotations

import json
from pathlib import Path

from schemashift.metrics.evaluate import (
    DEFAULT_CORPUS,
    evaluate_equivalence,
    evaluate_system,
    load_corpus,
    write_metrics_json,
)


def test_evaluate_system_without_services_marks_metrics_uncomputed() -> None:
    system = evaluate_system()
    assert system["result_set"]["computed"] is False
    assert system["ai"]["computed"] is False
    assert {"ecommerce", "blog", "university", "banking"} <= set(system["samples"])


def test_write_metrics_json_keeps_previous_sections(tmp_path: Path) -> None:
    metrics, _ = evaluate_equivalence(load_corpus(DEFAULT_CORPUS))
    target = tmp_path / "metrics.json"
    target.write_text(json.dumps({"result_set": {"computed": True, "rate": 0.9}}))
    write_metrics_json(metrics, evaluate_system(), target)
    data = json.loads(target.read_text())
    assert data["result_set"]["rate"] == 0.9  # not recomputed -> preserved
    assert data["equivalence"]["detection_rate"] >= 0.95
    assert set(data["equivalence"]["confusion"]) == {"SAFE", "CHANGED", "BROKEN"}
