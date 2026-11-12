"""Check that live AI works from this environment: one tiny request (a few tokens).

    uv run --env-file .env python -m tools.ai_check
    docker compose run --rm runner python -m tools.ai_check      # inside the demo setup

Uses the study's pinned model and ANTHROPIC_API_KEY (plus ANTHROPIC_WORKSPACE_ID for a
key that is not scoped to a workspace). Prints what the API said; exit code 1 on failure.
Also reports the AI mode and what has been spent against the budget cap.
"""

from __future__ import annotations

import sys

from adjud.ai.gateway import Gateway
from adjud.paths import DataPaths
from adjud.store import FileCaseStore


def main() -> int:
    paths = DataPaths.from_env()
    gw = Gateway.for_study(FileCaseStore(paths), paths.config, "DEMO01")
    ok, detail = gw.check()
    print(f"{'OK' if ok else 'FAILED'}: {detail}")
    print(
        f"AI mode: {gw.cfg.mode}. Spent ${gw.spent_usd():.4f} of the ${gw.cfg.budget_usd:.2f} cap."
    )
    if ok and gw.cfg.mode != "live":
        print("The key works, but agents call the API only in live mode (ADJUD_AI_MODE=live).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
