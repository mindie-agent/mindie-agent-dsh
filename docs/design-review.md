# Design-principles review (`0.0.1`)

Reviewed commit: the `v0.0.1` tree. Principles revision:
[`docs/design-principles.md`](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md)
(read 2026-10-17).

This is a concrete review of the shipped adapter, not a blanket “all
principles followed” claim.

| Principle | Conclusion and applicability |
| --- | --- |
| 1. Total cost | Host rows let users keep the `standard` preset; no mode switch, no copied preset to maintain. The one-time `setup.py` is explicit and idempotent. |
| 2. Closed tools only | Knowledge reads and feed sync are bounded operations. Open-ended engineering judgement stays in the skill/model. Feedback and contribution were removed rather than half-implemented. |
| 3. Minimal cognition | One user entry (`/mindie-agent`) and no subcommands. Setup prints one JSON summary with paths and next action. |
| 4. Single responsibility | Composition (`cordis.patch.yml`), paths (`paths.cjs` / `mindie_paths.py`), runtime (`setup.py`), MCP protocol (`knowledge_mcp.py`), and knowledge content (published components) are separate. |
| 5. Skill as a map | `SKILL.md` is under 8 KiB and defers details to three `references/` files loaded on demand. |
| 6. Knowledge is advisory | Tool descriptions, skill text, and result notes all state that references are fallible navigation, not authority. |
| 7. On-demand entry | `disable-model-invocation: true` + the DSH `/mindie-agent` gesture. `remote-dev` remains default-on, matching the product design. No capture or background publication. |
| 8. Reuse | Uses the shipped `dsh-skill-filesystem` and `dsh-mcp-client`, the component's own `remote_dev.mcp.server`, and `uv.lock` instead of a private installer. No custom Cordis Host plugin. |
| 9. Visible errors | Missing runtime/setup, service startup failure, unknown tool, unexpected arguments, capture attempts, and failed sync all return explicit errors. Setup reports `partial` when runtime is ready but sync failed. |
| 10. Gap-driven implementation | The only custom code is the knowledge MCP bridge (the component has no MCP server) and the setup/path helpers. |
| 11. Authorization decoupled | No contribution authorization exists in 0.0.1, so there is nothing to reset or silently change. |
| 12. Paired releases | Pins match the published `knowledge v0.11.0`, `remote-dev v0.9.5`, and `diagnostics v0.4.1` releases. |
| 13. Silent update boundary | This version deliberately has no updater. Upgrades are explicit: re-run setup and switch tags. |
| 14. `unknown` protection | Remote-dev's own `submission_state`/`category` error contract is preserved; the skill forbids automatic replay of uncertain writes. |

## Findings fixed during review

1. **A copied `mindie` preset was rejected.** It would duplicate the shipped
   standard composition and shadow future DSH preset changes. The final bundle
   inserts Host rows only and overrides no shipped row or preset.
2. **Automatic feed sync on the first query was removed.** It could hide a
   long network operation behind an ordinary read. `knowledge_sync` is now an
   explicit tool, and query errors tell the caller to invoke it once.
3. **The capture adapter cannot silently return empty.** The engine requires a
   transcript adapter, so the shipped stub raises
   `NotImplementedError("DSH public-transcript capture is not enabled...")`
   instead of pretending there was nothing to capture.
4. **Setup retires an existing service before rewriting the engine config.**
   Otherwise a configuration-fingerprint mismatch could block the next start.
   A failed stop is reported as a warning, not swallowed.
5. **The MCP launcher resolves `uv` from well-known locations.** A GUI-launched
   DSH may have a narrower PATH than a terminal; `MINDIE_DSH_UV` remains an
   override. The resolution is inlined in the patch after a live reinstall
   proved a cached CommonJS helper can survive a tag switch.
6. **A Host-level knowledge identity is documented as such.** Read-only calls
   do not pretend to have per-session leases.

## Remaining gaps

- Real remote Ascend execution and non-macOS platforms are unverified.
- `/mindie-agent` visual slash-menu rendering was not exercised by the agent.
- Capture, feedback, contribution, and reporting are intentionally absent.
