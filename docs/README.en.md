# `mindie-agent-dsh` Documentation Index

中文版：[`docs/README.md`](README.md)

This directory holds active verification contracts, microbenchmark notes, and formal release records for `mindie-agent-dsh`, the native MindIE Agent plugin for DeepSeek Harness (`DSH`). The `0.1.26` thin-plugin architecture (`paths.cjs`, `host.mjs`, `host3.mjs`, `scripts/{mindie_launch.py,dsh_transcript.py,setup.py}`) is documented in [`verification.md`](verification.md). Organization-wide architecture and design principles live in `mindie-agent/docs/`; repository operating rules and contributor gates live in [`AGENTS.md`](../AGENTS.md) and [`CONTRIBUTING.en.md`](../CONTRIBUTING.en.md).

## Active Documentation

- [`install/`](install/user-notice.md) — Installation and consent guides: [`user-notice.md`](install/user-notice.md) (contribution & fault-reporting notice), [`github-authentication.md`](install/github-authentication.md) (GitHub sign-in), and [`workspace-setup.md`](install/workspace-setup.md) (workspace setup).
- [`verification.md`](verification.md) — DSH host protocol boundaries, cross-platform verification matrix, and SDK interface audit.
- [`benchmark.md`](benchmark.md) — Release CPU microbenchmark (`benchmarks/release.py`) scope and execution guide.
- [`releases/`](releases/) — Formal release notes by tag (latest: [`0.1.26`](releases/mindie-agent-dsh-0.1.26.en.md)).

## Historical Archive

- [`archive/README.md`](archive/README.md) — Archived `0.0.1`/`0.0.2` milestone notes, early same-version refresh instructions, and initial design review preserved for provenance.
