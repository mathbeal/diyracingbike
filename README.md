# DIY Racing Bike — LLM-Powered Cycling Race Simulator

A didactic project exploring **autonomous LLM agents** in a turn-based cycling race. Each rider is an independent AI agent that decides its own strategy (advance, slow, draft, potion, wait) based on race state. The project ships two architectures and a CI pipeline that runs the simulation on every config change.

---

## Architecture

### `race.py` — Async agents (asyncio)

```
Orchestrator
  └── asyncio.gather(agent_A1, agent_B1, agent_C1, …)
        └── each agent: one Claude API call per tick → returns one action
```

- All 9 rider agents run **concurrently** via `asyncio.gather`
- The orchestrator collects all actions, feeds them into the deterministic `resolve()` engine, then broadcasts the new state
- One tick = one round of parallel API calls

### `race_bg.py` — Background agents (multiprocessing)

```
Main process (orchestrator)
  ├── 3 specialist processes (energy analysts, one per team)
  ├── 1 strategist process (global race view)
  └── 9 rider processes (one per cyclist)
        └── each polls specialists for advice, then calls Claude independently
```

- Each process has **isolated memory** (OS-level isolation)
- Inter-process communication via `multiprocessing.Queue`
- Specialists and riders run **in parallel across CPU cores**
- Riders consult specialists before deciding, then submit their action to the orchestrator's output queue

### Race engine constraints

- **Speed = energy** — a rider with energy 5 advances 5 cells per tick (±1 random variation)
- **Energy cost** — advancing costs `energy // 2` per tick
- **Draft window** — riding within 5 cells of a leader grants +1 energy recovery
- **Overtaking is allowed** — multiple riders can share the same cell
- **Potion** — one-time +3 energy boost, usable once per rider
- **Energy bounds** — clamped to [1, 5]
- **Starting positions** — randomized each race

---

## CI — PR-triggered async pipeline

Any pull request that modifies `race_config.json` automatically triggers a GitHub Actions workflow:

```
PR opened / updated
  └── .github/workflows/run-race.yml
        ├── Checkout PR branch
        ├── Run baseline race  (main branch config)   → baseline.json
        ├── Run PR race        (PR config)             → pr.json
        ├── scripts/compare_results.py baseline.json pr.json
        └── Post Markdown comparison as PR comment
```

The comment shows winner, tick count, changed initial energies, and impact analysis.

> The workflow uses `ANTHROPIC_API_KEY` from GitHub Secrets — the key is **never** in the source code.

---

## Installation

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
git clone git@github.com:mathbeal/diyracingbike.git
cd diyracingbike

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# Create a .env file (never committed)
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### Run the async race (asyncio)

```bash
python race.py
```

With a custom config:

```bash
python race.py --config race_config.json --output results.json --no-interactive
```

### Run the background-agents race (multiprocessing)

```bash
python race_bg.py
```

### Run tests

```bash
pytest
```

---

## Configuration

Edit `race_config.json` to change the race setup:

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
    }
  ]
}
```

Push a branch with a modified `race_config.json` and open a PR to see the CI pipeline compare the outcomes automatically.

---

## Project structure

```
race.py                  # Async agents (asyncio.gather)
race_bg.py               # Background agents (multiprocessing)
race_config.json         # Default race configuration
requirements.txt
scripts/
  compare_results.py     # PR comment generator
tests/
  test_race.py           # Unit tests for the race engine
.github/workflows/
  run-race.yml           # CI pipeline
```
