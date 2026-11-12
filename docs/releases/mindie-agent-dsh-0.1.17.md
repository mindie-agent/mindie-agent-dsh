# mindie-agent-dsh 0.1.17

The coordinator tool catalog and MindIE entry guidance now distinguish native
call identity, optional knowledge activation and managed task attachment.
Host inventory, probes, containers and bare-metal leases can run without a
managed task binding. Source/model discovery, CPU-only tests, offline profiler
analysis and simulation continue independently of device allocation.

Device work retains real ownership and lease requirements. Missing ownership
pauses only the dependent action; uncertain external writes are reconciled before
replacement. Runtime dependency pins and user consent remain unchanged.

The source compatibility declaration now uses the canonical shared schema
consumed by native updaters and the shared release publisher. The publisher
automatically generates the release metadata from the tested commit. Runtime
setup reads its component versions from the existing project dependencies and
lockfile, removing the duplicated runtime pin list from the compatibility file.
