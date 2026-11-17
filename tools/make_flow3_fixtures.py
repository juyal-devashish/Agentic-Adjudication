"""Answer sets for the Flow 3 demo (plan P-52). Synthetic data only.

    uv run python -m tools.make_flow3_fixtures --out data/demo_uploads/flow3
    uv run python -m tools.make_flow3_fixtures --apply disputed --case DEMO01-0003

Each scenario is a YAML file with the answers of Adjudicators 1, 2 and 3 and the
committee's decisions, and the route the case should take:

    agree       both reviews give the same answers (dates within the 1-day tolerance)
                → outcome straight away
    disputed    the reviews disagree on the MI type and on relatedness; Adjudicator 3
                matches one review on the MI type, not on relatedness → committee

`--apply` signs the open reviews of a case with a scenario's answers, as the assigned
demo adjudicators (password `demo`), so a presenter need not fill every form by hand.
It acts through the same Flow 3 functions as the pages, one signature per review, and
only for reviews still open. The committee's meeting is left for the presenter.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

BASE = {"event_confirmed": "yes", "mi_type": "type_1", "event_date": "2026-03-14",
        "related_to_study_drug": "not_related"}  # fmt: skip

SCENARIOS = {
    "agree": {
        "route": "agreed → outcome",
        "adjudicator_1": {**BASE, "rationale": "Troponin rise with ST elevation; occluded RCA."},
        "adjudicator_2": {
            **BASE,
            "event_date": "2026-03-15",
            "rationale": "Spontaneous MI, type 1.",
        },  # fmt: skip
    },
    "disputed": {
        "route": "disputed → Adjudicator 3 resolves the MI type → committee on relatedness",
        "adjudicator_1": {
            **BASE,
            "related_to_study_drug": "related",
            "rationale": "Onset two weeks after the dose increase.",
        },  # fmt: skip
        "adjudicator_2": {
            **BASE,
            "mi_type": "type_2",
            "rationale": "Supply-demand mismatch during anaemia.",
        },  # fmt: skip
        "adjudicator_3": {"mi_type": "type_1", "related_to_study_drug": "unable_to_assess"},
        "committee": {"related_to_study_drug": "not_related"},
    },
}


def make(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, scenario in SCENARIOS.items():
        path = out / f"{name}.yaml"
        path.write_text(yaml.safe_dump(scenario, sort_keys=False, allow_unicode=True))
        written.append(path)
    return written


def apply(case_id: str, scenario: str) -> list[str]:
    """Sign the case's open reviews with the scenario's answers. Returns who signed."""
    from adjud.flow3 import Flow3, current_round
    from adjud.paths import DataPaths
    from adjud.store import FileCaseStore

    paths = DataPaths.from_env()
    flow3 = Flow3(FileCaseStore(paths=paths), paths.config)
    rnd = current_round(flow3.store, case_id)
    if rnd is None:
        raise SystemExit(f"{case_id} has no review assigned yet")
    data = SCENARIOS[scenario]
    signed = []
    for slot, user in sorted(rnd.holders.items()):
        if slot in rnd.signed or flow3.slot_of(case_id, user) != slot:
            continue
        answers = data.get(f"adjudicator_{slot}")
        if answers is None:
            continue
        flow3.sign_review(case_id, user, "demo", answers)
        signed.append(f"Adjudicator {slot}: {flow3.users()[user]['name']}")
    return signed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the scenarios to this folder")
    parser.add_argument("--apply", choices=list(SCENARIOS), help="sign a case's open reviews")
    parser.add_argument("--case", help="the case for --apply, e.g. DEMO01-0003")
    args = parser.parse_args()
    if args.out:
        for p in make(args.out):
            print("wrote", p)
    if args.apply:
        if not args.case:
            parser.error("--apply needs --case")
        print("\n".join(apply(args.case, args.apply)) or "no open review to sign")


if __name__ == "__main__":
    main()
