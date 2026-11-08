"""Stage 0: load and validate one experiment YAML.

An Experiment is everything that defines *what* is run. How many times it is
run (n_runs), how many run at once (jobs) and the spend cap (budget_usd) are
launch arguments to `run_agent_team`, not part of the experiment.

Each run records `experiment_hash()` and `flat()` so any result row can be
joined back to the exact config it ran with.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Model = Literal["haiku", "sonnet", "opus"]  # aliases; each backend maps them to model ids


class _Strict(BaseModel):
    # extra="forbid": a typo'd knob is an error, never a silently ignored default
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rule(_Strict):
    level: int = Field(ge=0)  # index into rules/ladder.yaml (0 harmless marker, 3 skip tests)
    seed_location: Literal["notes", "agents_md"] = "notes"
    seed_seat: Optional[Literal["boss", "worker"]] = "worker"  # whose notes; unused for agents_md

    @model_validator(mode="after")
    def _seat_matches_location(self) -> "Rule":
        if self.seed_location == "notes" and self.seed_seat is None:
            raise ValueError("seed_location=notes needs a seed_seat (boss or worker)")
        if self.seed_location == "agents_md" and self.seed_seat is not None:
            # file starts marked, no index case: pi0 is undefined for these runs
            raise ValueError("seed_location=agents_md has no seed_seat; set it to null")
        return self


class Team(_Strict):
    boss_model: Model
    worker_models: list[Model] = Field(min_length=1)
    # v1 is flat only. Turn order is fixed, not a knob: each round the boss goes
    # first, then workers in listed order, sequentially (as in Shilp's driver).
    structure: Literal["flat"] = "flat"
    messaging: bool = True  # all-to-all via inbox/<name>.md files


class Defenses(_Strict):
    invite_sentence: bool = True  # AGENTS.md line: "record new team rules in this file"
    clean_every_k: Optional[int] = Field(default=None, ge=1)  # oracle wipe of AGENTS.md


class Plumbing(_Strict):
    backend: Literal["anthropic_api", "agent_sdk", "claude_cli"] = "anthropic_api"
    effort: Literal["low", "medium", "high", "default"] = "default"
    max_turns: int = Field(default=20, ge=1)  # tool-loop cap per agent call


class Experiment(_Strict):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    task: str = "tinyutils"  # starter workspace under tasks/
    n_rounds: int = Field(default=8, ge=1)
    rule: Rule
    team: Team
    defenses: Defenses = Defenses()
    plumbing: Plumbing = Plumbing()

    def experiment_hash(self) -> str:
        """Stable id for this exact config (name included)."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def flat(self) -> dict:
        """One row of dotted columns, for runs.csv."""
        row: dict = {}

        def walk(prefix: str, obj: dict) -> None:
            for k, v in obj.items():
                key = f"{prefix}{k}"
                if isinstance(v, dict):
                    walk(key + ".", v)
                elif isinstance(v, list):
                    row[key] = ",".join(map(str, v))
                else:
                    row[key] = v

        walk("", self.model_dump(mode="json"))
        row["team.n_workers"] = len(self.team.worker_models)
        row["experiment_hash"] = self.experiment_hash()
        return row


def load_experiment(path: str | Path) -> Experiment:
    return Experiment.model_validate(yaml.safe_load(Path(path).read_text()))
