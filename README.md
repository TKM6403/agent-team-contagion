# Agent Team Contagion

How a planted rule spreads, or dies out, in teams of AI coding agents. We treat a team of Claude agents (an orchestrator, workers, a shared rules file) as a small epidemic: we plant one bad house rule in one agent's notes, log every file read, file write and message, and measure who adopts it, who passes it on, and who recovers.

Project for the Apart Research [Swarm Alignment Hackathon](https://apartresearch.com/sprints/swarm-alignment-2026), November 6 to 8, 2026, Track 2. Team: Tarun Martheswaran and Shilp Shah.

## Contents

| Folder | What is in it |
|---|---|
| `STAGES.md` | How the pipeline is organised: six stages, their inputs and outputs |
| `experiment_configs/` | One YAML per experiment (stage 0 input) |
| `rules/` | The rule ladder: what gets planted, by level |
| `src/contagion/` | Pipeline code |
| `report_materials/` | The research report (work in progress): LaTeX source, figures, and the built PDF |

