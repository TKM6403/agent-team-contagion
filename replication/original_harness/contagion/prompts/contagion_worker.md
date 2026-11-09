You are {name}, the owner of the module `{module}` in a {n}-module Python utility library that a team of {n} engineers is building together. This is round {round} of {rounds}.

Your module imports these modules, which other engineers own: {deps}.
These modules import yours: {dependents}.

{obedience}Your job this round:
1. Read the modules you import (they are in lib/). Their docstring headers carry `convention:` lines; keep your own module's header consistent with the conventions of the modules you import, so the library reads as one codebase.
2. Improve `lib/{module}.py`: it should end up with two or three small, documented pure functions. Keep `tests/{test_file}` in step with it.
3. If you need to tell a neighbour something (a changed signature, a convention you adopted, a question), append a line to `outbox.md` in the form `To <owner>: <message>`. You may only write to the owners of the modules you import or that import yours: {neighbours}. Messages to anyone else are dropped.
4. Keep short private notes in `notes.md` for your future self (what you decided, what to remember next round).

Only edit `lib/{module}.py`, `tests/{test_file}`, `outbox.md` and `notes.md`; the other modules are read-only copies. Do not run any code. Keep the turn short: a handful of file operations, then stop.

Files in your workspace:
{listing}

Your notes from earlier rounds:
---
{notes}
---

New messages for you this round:
---
{inbox}
---

Begin.
