# Native public-check qualification

The explicit Python public-check backend passed two fresh authored controls on 9 November 2026. Source: `f59eccda117c945f697d1dd3c173fc37883ea3a3`, tree `b4f3ac21b40d2a06af56279f5285c183f90a530f`, delivered in [PR89](https://github.com/desanv01/FollowThrough/pull/89) with passing Ubuntu and Windows source CI.

| Authored control | Public-check result | Exit | Cleanup | Owned container absent |
| --- | --- | --- | --- | --- |
| R3 correct candidate | Pass | 0 | Settled | Verified |
| R1 incorrect initial candidate | Fail | 1 | Settled | Verified |

The actual receipts bind each request, candidate hash and requirements version. Native inspection verified both pre-start and post-exit identity, image, exact two read-only mounts, network isolation, nonroot execution, dropped capabilities, no-new-privileges, read-only root, PID/memory/CPU/tmpfs limits, checker command and exit status. Independent full-ID/name Docker readbacks confirmed the owned containers absent. The shared lease was released. No model/provider call occurred.

See the [sanitized control record](native-controls-2026-11-09.json) and [backend usage](../project/PYTHON-CHECK.md). Private raw receipts and host/container listings are intentionally excluded from this public record. The retained receipt hashes bind the published summary to the inspected private artifacts.

The first control harness stopped at strict request validation before any backend or Docker invocation because it supplied an extra schema field. That pre-dispatch attempt was preserved and settled; the two controls above used fresh corrected request identities. No production source was changed to relax request validation.

The original Rust-spawned Docker setup error remains unresolved on this host. Fixed Docker info passes when Python launches it directly but fails through Rust even with matching captured environment values and working directory. Targeted executable-path, handle and stdin comparisons did not establish a cause. No ACL, daemon, system or elevation change was made. The Python backend is an explicit supported option, not evidence that the Rust problem is fixed.

Separately, the read-only T3 viewer displayed the saved real development report in a native terminal. Evidence view, a no-match filter, exit and cursor restoration worked. Its one-row input did not test movement between multiple attempts. The saved episode remains infrastructure-error/excluded, with protected functional pass and other dimensions unknown.

These controls qualify this backend's authored native path on this host. They are not a completed model episode, pilot, formal dataset, full abnormal-lifecycle audit or all-platform Docker qualification. Provider evaluation and study results must be recorded separately.
