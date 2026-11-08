"""The rule ladder: turns `rule.level` + `rule.marker` into the exact text that gets planted.

The ladder lives in rules/ladder.yaml. Each rung is one house rule. Higher
rungs fight harder against doing the task well, so comparing adoption across
rungs asks: do agents resist rules that hurt the work?
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_LADDER = Path(__file__).resolve().parents[2] / "rules" / "ladder.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Level(_Strict):
    """One rung of the ladder: a single house rule."""

    level: int = Field(ge=0)
    """Rung number, used by `rule.level` in an experiment config. 0 is the
    mildest. Don't renumber existing rungs: run records refer to them."""

    do: str
    """The instruction itself, as the agent reads it. Must contain `{marker}`
    so that obeying the rule leaves the marker word in the agent's output;
    that is how stage 2 detects infection with a text search."""

    resists: str
    """For researchers only; never shown to agents. Why a well-behaved agent
    might push back on this rule (e.g. "breaks the spec"). It documents what
    makes each rung harder to adopt than the one below."""


class Ladder(_Strict):
    """The full ladder plus the wrapper sentence every planted rule shares."""

    template: str
    """Wraps a rung into the planted text. Has `{source}`, `{do}` and `{promote}` slots."""

    source: dict[Literal["worker", "boss"], str]
    """Who the rule claims to come from, by patient zero's seat. A worker's
    copy says it comes from the boss; the boss's copy names no source, since
    nobody is above the boss."""

    promote: str
    """The part of the rule that asks to spread: tells whoever holds the rule
    to copy it into AGENTS.md. Not to be confused with the `invite_sentence`
    defense, which is a line in AGENTS.md itself."""

    levels: list[Level] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "Ladder":
        nums = [lv.level for lv in self.levels]
        if len(nums) != len(set(nums)):
            raise ValueError(f"duplicate levels: {nums}")
        for lv in self.levels:
            if "{marker" not in lv.do:
                raise ValueError(f"level {lv.level}: `do` must contain {{marker}} so obeying it is detectable")
        return self

    def get(self, level: int) -> Level:
        for lv in self.levels:
            if lv.level == level:
                return lv
        raise KeyError(f"rule level {level} not in ladder (have {[lv.level for lv in self.levels]})")

    def do_text(self, level: int, marker: str) -> str:
        """Just the instruction, with the marker filled in."""
        return self.get(level).do.format(marker=marker, marker_lower=marker.lower())

    def rule_text(self, level: int, marker: str, seat: Literal["worker", "boss"]) -> str:
        """The exact text planted in patient zero's notes."""
        return self.template.format(source=self.source[seat], do=self.do_text(level, marker), promote=self.promote)

    def ladder_hash(self) -> str:
        """12-character hash of the ladder contents. Recorded with every run, so
        editing a rung's wording can never silently change what "level 3" means."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


def load_ladder(path: str | Path = DEFAULT_LADDER) -> Ladder:
    return Ladder.model_validate(yaml.safe_load(Path(path).read_text()))
