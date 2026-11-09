"""The import graph of a graph team: which module imports which.

Deterministic from (n_agents, mean_imports, seed): module i imports
min(i, mean_imports) of the modules before it, chosen uniformly at random.
So the imports form a DAG (no cycles), early modules are imported most, and
the hub (most-imported module) is one of the first few. Modules are drawn in
order from one RNG, so growing n_agents with the same seed and mean_imports
keeps every earlier module's imports unchanged: a 100-module graph contains
the 20-module graph with the same settings as its first 20 modules.

Contact is directed: an agent reads the modules it imports. Messages (if on)
may go both ways along an edge, to `neighbours` = imports + importers.

Module names are m00, m01, ... (three digits from 100 modules up). In a graph
team the agent that owns a module has the module's name.

Limitations
-----------
- One graph family (uniform random recursive DAG). Ring, small-world and
  preferential-attachment graphs (the teammate's harness has them) are not
  implemented; add a `kind` field to config.Graph to grow this.
- In-degree is skewed toward early modules by construction, so "hub" is
  always an early module.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path


def module_name(i: int, n: int) -> str:
    return f"m{i:0{max(2, len(str(n - 1)))}d}"


@dataclass(frozen=True)
class ImportGraph:
    n: int
    imports_idx: tuple[tuple[int, ...], ...]   # imports_idx[i] = modules i imports (indices)

    @cached_property
    def names(self) -> list[str]:
        return [module_name(i, self.n) for i in range(self.n)]

    @cached_property
    def _index(self) -> dict[str, int]:
        return {m: i for i, m in enumerate(self.names)}

    @cached_property
    def _importers_idx(self) -> list[list[int]]:
        out: list[list[int]] = [[] for _ in range(self.n)]
        for i, deps in enumerate(self.imports_idx):
            for d in deps:
                out[d].append(i)
        return out

    def imports_of(self, m: str) -> list[str]:
        """Modules `m` imports (it reads read-only copies of these)."""
        return [self.names[d] for d in self.imports_idx[self._index[m]]]

    def importers_of(self, m: str) -> list[str]:
        """Modules that import `m` (they read `m`)."""
        return [self.names[i] for i in self._importers_idx[self._index[m]]]

    def neighbours(self, m: str) -> list[str]:
        """Imports and importers, sorted: who `m` may message."""
        return sorted(set(self.imports_of(m)) | set(self.importers_of(m)))

    @cached_property
    def hub(self) -> str:
        """The most-imported module; ties go to the lowest index."""
        return self.names[max(range(self.n), key=lambda i: (len(self._importers_idx[i]), -i))]

    def edges(self) -> list[tuple[str, str]]:
        """(importer, imported) pairs."""
        return [(self.names[i], self.names[d]) for i, deps in enumerate(self.imports_idx) for d in deps]

    def save(self, path: Path, **extra) -> None:
        path.write_text(json.dumps({"n": self.n, "modules": self.names, "hub": self.hub,
                                    "edges": self.edges(), **extra}, indent=1))


@lru_cache(maxsize=64)
def build_graph(n: int, mean_imports: int, seed: int) -> ImportGraph:
    rng = random.Random(seed)
    imports = []
    for i in range(n):
        k = min(i, mean_imports)
        imports.append(tuple(sorted(rng.sample(range(i), k))) if k else ())
    return ImportGraph(n, tuple(imports))


def graph_of(exp) -> ImportGraph:
    """The import graph an Experiment with team.structure == graph describes."""
    g = exp.team.graph
    return build_graph(g.n_agents, g.mean_imports, g.seed)
