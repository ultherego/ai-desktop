# Tests

```sh
python3 -B scripts/verify.py --mode ci --fail-fast     # syntax, unit tests, no AI provider
python3 -B scripts/verify.py --mode local --fail-fast  # additionally real Bubblewrap probes on this computer
```

`--json NEW_FILE` saves stage names and exit codes, without logs and without overwriting. The `compat` and `ai` modes require explicit flags (`--allow-claude-compat`, `--allow-ai-request`), and `ai` uses your subscription. Green tests do not prove isolation on your computer.
