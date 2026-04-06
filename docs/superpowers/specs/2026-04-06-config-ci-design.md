# Cycling Race — Config File + GitHub Action CI

**Date :** 2026-04-06  
**Objectif :** Permettre à chaque PR de définir des conditions initiales différentes (énergie, piste, équipes), lancer la course automatiquement, et poster une comparaison baseline vs PR en commentaire.

Cela permet d'utiliser `race.py` comme démonstrateur de l'Exercise 1 "async platform agent workflow" : chaque PR est une expérience, l'agent CI tourne en background, le résultat est reviewé dans GitHub.

---

## 1. Nouveaux fichiers

```
diyracingbike/
├── race.py                          # modifié : --config, --output, --no-interactive
├── race_config.json                 # config par défaut (baseline)
├── scripts/
│   └── compare_results.py          # génère le commentaire Markdown de comparaison
└── .github/
    └── workflows/
        └── run-race.yml            # GitHub Action déclenché sur PR
```

---

## 2. `race_config.json` — format

```json
{
  "track_length": 60,
  "teams": [
    {
      "name": "A",
      "riders": [
        {"id": "A1", "energy": 5},
        {"id": "A2", "energy": 5},
        {"id": "A3", "energy": 5}
      ]
    },
    {
      "name": "B",
      "riders": [
        {"id": "B1", "energy": 5},
        {"id": "B2", "energy": 5},
        {"id": "B3", "energy": 5}
      ]
    },
    {
      "name": "C",
      "riders": [
        {"id": "C1", "energy": 5},
        {"id": "C2", "energy": 5},
        {"id": "C3", "energy": 5}
      ]
    }
  ]
}
```

**Contraintes de validation :**
- `track_length` : entier entre 20 et 200
- Chaque `energy` : entier entre 1 et 5
- Exactement 3 équipes de 3 cyclistes (contrainte fixe pour le MVP)
- Les IDs des cyclistes doivent être uniques

---

## 3. Modifications de `race.py`

### Nouveaux arguments CLI

```bash
python race.py [--config PATH] [--output PATH] [--no-interactive]
```

- `--config PATH` (défaut: `race_config.json`) — charge les conditions initiales
- `--output PATH` (optionnel) — écrit les résultats en JSON
- `--no-interactive` — supprime le `input("Appuyez sur Entrée...")` pour les runs CI

### `load_config(path) -> dict`

```python
def load_config(path: str) -> dict:
    """Charge et valide race_config.json. Lève ValueError si invalide."""
    with open(path) as f:
        config = json.load(f)
    validate_config(config)  # lève ValueError si problème
    return config
```

### `init_race_from_config(config) -> RaceState`

Remplace `init_race(track_length, teams, riders_per_team)` quand une config est fournie. Crée les cyclistes avec les énergies définies dans le JSON.

### `write_results(state, decisions_log, path)`

Écrit en JSON :
```json
{
  "winner": "A",
  "ticks": 42,
  "finished_order": ["A2", "A1", "A3", "B1", "C2", ...],
  "config_summary": {
    "track_length": 60,
    "initial_energies": {"A1": 5, "A2": 3, "A3": 5, "B1": 5, ...}
  }
}
```

`decisions_log` est une liste de `{tick, decisions: {cyclist_id: action}}` accumulée dans la boucle principale.

### Modification de `main()`

```python
async def main() -> None:
    args = parse_args()  # argparse
    
    config = load_config(args.config)
    state = init_race_from_config(config)
    
    if not args.no_interactive:
        print(render(state))
        input("\nAppuyez sur Entrée pour lancer la course...")
    
    decisions_log = []
    
    while not race_over(state):
        actions = await orchestrator(state)
        decisions_log.append({"tick": state.tick, "decisions": dict(actions)})
        state = resolve(state, actions)
        if not args.no_interactive:
            print(render(state))
        await asyncio.sleep(0 if args.no_interactive else 0.3)
    
    if args.output:
        write_results(state, decisions_log, args.output)
    
    if not args.no_interactive:
        print(f"🏆 Vainqueur : Équipe {winner(state)}")
```

---

## 4. `scripts/compare_results.py`

```bash
python scripts/compare_results.py baseline.json pr.json
```

Produit sur stdout un commentaire Markdown :

```markdown
## 🚴 Résultats de course — comparaison

| | Baseline | Cette PR |
|---|---|---|
| **Vainqueur** | Équipe B | Équipe A |
| **Ticks** | 42 | 38 |
| **Ordre d'arrivée** | B1, B2, A1, A3, B3, C1, C2, A2, C3 | A2, A1, A3, C3, ... |

### Conditions initiales modifiées

| Cycliste | Baseline | PR | Δ |
|---|---|---|---|
| A1 | 5 | 3 | -2 |
| B2 | 5 | 5 | 0 |

### Analyse de l'impact

Les cyclistes de l'équipe A démarrent avec moins d'énergie mais gagnent 4 ticks plus tôt.
Cela suggère que les agents adoptent une stratégie de draft plus agressive dès le départ.
```

Le script calcule automatiquement le diff des conditions initiales en comparant les `config_summary` des deux fichiers JSON.

---

## 5. `.github/workflows/run-race.yml`

```yaml
name: Race Simulation

on:
  pull_request:
    paths:
      - 'race_config.json'   # ne se déclenche que si la config change

jobs:
  race:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write    # pour poster le commentaire
    
    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run baseline race
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          git show origin/main:race_config.json > race_config_baseline.json
          python race.py --config race_config_baseline.json \
                         --output baseline.json \
                         --no-interactive
      
      - name: Run PR race
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          python race.py --config race_config.json \
                         --output pr.json \
                         --no-interactive
      
      - name: Generate comparison comment
        run: python scripts/compare_results.py baseline.json pr.json > comment.md
      
      - name: Post PR comment
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const comment = fs.readFileSync('comment.md', 'utf8');
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: comment
            });
```

**Note :** Le workflow ne se déclenche que si `race_config.json` est modifié dans la PR (filtre `paths`). Cela évite des runs inutiles sur des PRs de documentation.

---

## 6. Points pédagogiques pour Exercise 1

| Étape | Surface async | Ce que ça démontre |
|---|---|---|
| Ouvrir une PR avec `race_config.json` modifié | GitHub PR | Kickoff d'un agent via code hosting platform |
| GitHub Action se déclenche | CI/CD | Agent travaille en background |
| Deux runs séquentiels (baseline + PR) | Workflow steps | Comparaison automatisée |
| Commentaire posté automatiquement | PR comment | "Review the agent's work" |
| Itérer sur les conditions | Nouvelles PRs | "Drive it to completion" |

---

## 7. Hypothèses et limites

- La course est déterministe à conditions égales ? Non — les agents Claude peuvent varier. C'est volontaire : les résultats ne sont pas reproductibles à 100%, ce qui reflète la réalité des agents LLM.
- Coût API : chaque run = 9 agents × N ticks × 1 appel Haiku. Pour track_length=60, estimé ~30-50 ticks → ~300-450 appels Haiku (~$0.01-0.02 par run).
- `race_config_baseline.json` : récupéré depuis `origin/main` avec `git show` — si la branche main n'a pas de `race_config.json`, le run baseline échoue gracieusement avec un message d'erreur explicite.
