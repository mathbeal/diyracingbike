#!/usr/bin/env python3
"""
compare_results.py — Génère un commentaire Markdown comparant deux runs de course.

Usage:
    python scripts/compare_results.py baseline.json pr.json
    → imprime le commentaire Markdown sur stdout
"""

import json
import sys


def generate_comment(baseline: dict, pr: dict) -> str:
    """Génère le commentaire Markdown de comparaison."""
    lines = []
    lines.append("## 🚴 Résultats de course — comparaison")
    lines.append("")

    # Tableau principal
    lines.append("| | Baseline | Cette PR |")
    lines.append("|---|---|---|")
    lines.append(f"| **Vainqueur** | Équipe {baseline['winner']} | Équipe {pr['winner']} |")
    lines.append(f"| **Ticks** | {baseline['ticks']} | {pr['ticks']} |")

    b_order = ", ".join(baseline["finished_order"][:5]) + ("..." if len(baseline["finished_order"]) > 5 else "")
    p_order = ", ".join(pr["finished_order"][:5]) + ("..." if len(pr["finished_order"]) > 5 else "")
    lines.append(f"| **Ordre d'arrivée (top 5)** | {b_order} | {p_order} |")
    lines.append("")

    # Diff des conditions initiales
    b_energies = baseline["config_summary"]["initial_energies"]
    p_energies = pr["config_summary"]["initial_energies"]

    changed = {
        cid: (b_energies.get(cid, "?"), p_energies.get(cid, "?"))
        for cid in set(b_energies) | set(p_energies)
        if b_energies.get(cid) != p_energies.get(cid)
    }

    if changed:
        lines.append("### Conditions initiales modifiées")
        lines.append("")
        lines.append("| Cycliste | Baseline | PR | Δ |")
        lines.append("|---|---|---|---|")
        for cid in sorted(changed):
            b_e, p_e = changed[cid]
            delta = p_e - b_e if isinstance(p_e, int) and isinstance(b_e, int) else "?"
            sign = "+" if isinstance(delta, int) and delta > 0 else ""
            lines.append(f"| {cid} | {b_e} | {p_e} | {sign}{delta} |")
        lines.append("")

        # Analyse automatique
        tick_diff = pr["ticks"] - baseline["ticks"]
        faster = "plus rapide" if tick_diff < 0 else "plus lente" if tick_diff > 0 else "identique en durée"
        winner_changed = baseline["winner"] != pr["winner"]
        lines.append("### Analyse de l'impact")
        lines.append("")
        if winner_changed:
            lines.append(
                f"Le changement de conditions initiales a **modifié le vainqueur** "
                f"(Équipe {baseline['winner']} → Équipe {pr['winner']})."
            )
        else:
            lines.append(f"Le vainqueur reste l'Équipe {pr['winner']}.")
        lines.append(
            f"La course est {faster} ({abs(tick_diff)} tick{'s' if abs(tick_diff) != 1 else ''} "
            f"{'de moins' if tick_diff < 0 else 'de plus' if tick_diff > 0 else ''}).".strip()
        )
    else:
        lines.append("*Aucune modification des conditions initiales — résultats potentiellement différents due à la variabilité des agents LLM.*")

    lines.append("")
    lines.append("---")
    lines.append("*Généré automatiquement par `scripts/compare_results.py`*")

    return "\n".join(lines)


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/compare_results.py baseline.json pr.json", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        baseline = json.load(f)
    with open(sys.argv[2]) as f:
        pr = json.load(f)

    print(generate_comment(baseline, pr))


if __name__ == "__main__":
    main()
