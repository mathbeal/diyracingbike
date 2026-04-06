import json
import os
import tempfile
import pytest
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.compare_results import generate_comment


BASELINE = {
    "winner": "B",
    "ticks": 42,
    "finished_order": ["B1", "B2", "A1", "A3", "B3", "C1", "C2", "A2", "C3"],
    "config_summary": {
        "track_length": 60,
        "initial_energies": {"A1": 5, "A2": 5, "A3": 5, "B1": 5, "B2": 5, "B3": 5, "C1": 5, "C2": 5, "C3": 5}
    },
    "decisions_log": []
}

PR_RESULT = {
    "winner": "A",
    "ticks": 38,
    "finished_order": ["A2", "A1", "A3", "C3", "B1", "C1", "B2", "B3", "C2"],
    "config_summary": {
        "track_length": 60,
        "initial_energies": {"A1": 5, "A2": 3, "A3": 5, "B1": 5, "B2": 5, "B3": 5, "C1": 5, "C2": 5, "C3": 5}
    },
    "decisions_log": []
}


def test_generate_comment_returns_markdown():
    comment = generate_comment(BASELINE, PR_RESULT)
    assert isinstance(comment, str)
    assert "##" in comment  # titre markdown


def test_generate_comment_shows_winner():
    comment = generate_comment(BASELINE, PR_RESULT)
    assert "Équipe B" in comment or "B" in comment
    assert "Équipe A" in comment or "A" in comment


def test_generate_comment_shows_ticks():
    comment = generate_comment(BASELINE, PR_RESULT)
    assert "42" in comment
    assert "38" in comment


def test_generate_comment_shows_changed_energies():
    comment = generate_comment(BASELINE, PR_RESULT)
    # A2 passe de 5 à 3
    assert "A2" in comment


def test_generate_comment_no_changes_when_identical():
    comment = generate_comment(BASELINE, BASELINE)
    assert "identique" in comment.lower() or "aucune" in comment.lower() or "no change" in comment.lower()


def test_generate_comment_cli(tmp_path):
    """Test le script en ligne de commande."""
    baseline_path = tmp_path / "baseline.json"
    pr_path = tmp_path / "pr.json"
    baseline_path.write_text(json.dumps(BASELINE))
    pr_path.write_text(json.dumps(PR_RESULT))

    import subprocess
    result = subprocess.run(
        ["python3", "scripts/compare_results.py", str(baseline_path), str(pr_path)],
        capture_output=True, text=True,
        cwd="/home/mathieu/diyracingbike"
    )
    assert result.returncode == 0
    assert "##" in result.stdout
