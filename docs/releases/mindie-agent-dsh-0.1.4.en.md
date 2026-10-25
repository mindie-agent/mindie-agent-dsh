# mindie-agent-dsh 0.1.4

Pins remote-dev `4c40cb5`, aligns diagnostics `0.5.4` (`5ae643e`), and keeps DSH per-session isolation, the 660000 ms host timeout, and bounded head/tail plus full refs for large logs. Diagnostics 0.5.4 allocates the first/free log slot with `O_EXCL` before reader-registry/cursor coordination, fixing the old ~101 ms setup CI regression; full-window/pressure semantics are unchanged and no asynchronous redesign is claimed. Preview failures preserve the real command result and are explicitly marked degraded.

`host.mjs` is loaded by the DSH host at startup. After installing a native package the updater sets `needs_host_reload=true`. Existing sessions keep the old loaded code and are not killed; a new generation's `host3` only switches while idle, and the `host.mjs` body changes only on a natural host reload/new process. The receipt does not claim installed means active.

No formal tag/release is made; historical verified evidence retains its original version rather than being relabeled 0.1.4.
