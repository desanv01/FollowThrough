# Explicit Python public-check backend

`episode.live --check-backend python` selects the Python Docker public-check
executor. The default remains `rust`; this option does not qualify or repair the
Rust subprocess path. Source acceptance, authored native qualification and paid
operation authorization remain separate gates.

The selected operation's reviewed G4 must bind its source commit/tree as usual and
contain `runtime.check_backend = "python"` and `runtime.python_check_sha256` equal
to the SHA-256 of `episode/python_check.py`. A missing or mismatched backend hash
refuses before credential loading or provider construction. Older Rust gates keep
their existing meaning. `--check-executable` is required only for the Rust backend;
Docker, image and protected-grader component verification still applies.

The existing `LiveCheckBridge` still validates the request and candidate SHA,
records the fresh run/version/inventory binding, reads bounded private evidence,
and projects the unchanged check-port schema. The Python runner produces a
`container-check-0.1` receipt labeled `real_container_check` with private
`backend = "python-docker-public-0.1"` and its source hash. A fake injected command
is an offline synthetic test control and is never native qualification evidence.

Each check creates one stopped container from the fixed pinned Python image,
without pulling. Only the selected candidate (`processor.py` or `preview.py`) and
`public_checks.py` are mounted, as separate read-only files under `/workspace`.
The fixed command is `python -I -B /workspace/public_checks.py`. No candidate code
is executed on the host. Context, protected grading material, credentials and
private coordination are not mounted.

The executor uses the established DockerSession controls: no network, a read-only
root, all capabilities dropped, no new privileges, UID/GID 65534, 256 MiB RAM,
one CPU, 64 processes and a 16 MiB noexec/nosuid `/tmp`. Before start it checks the
actual typed Docker inspect values, both exact mount sources/destinations, image
ID, command, user and ownership labels. The bounded private receipt retains that
witness and the post-attach inspect. A pass/fail requires an exited container,
valid start/finish times, no OOM/error and a consistent inspected/attach exit code.

Each stream is bounded to 32 KiB. The executor's absolute allowance is at most
90 seconds, including termination, direct-child reap, reader settlement and a
five-second cleanup reserve. Every cleanup command shares one five-second
deadline. It removes only a full container ID whose exact name and unpredictable
owner/run labels were inspected. Removal is followed by no-trunc ID, name and
owner-label absence queries. An ambiguous create is unknown until an owned
container is discovered and verified. Unknown process, output-reader or cleanup
settlement remains an infrastructure error and preserves the outer campaign
lease. The existing outer lease and spending guard are unchanged.

Limitations remain explicit: local trusted Docker daemon, stable host filesystem,
direct-child process settlement, and public checks as feedback rather than hidden
functional or workflow grading. Native positive and negative authored controls
must pass before this backend can authorize a paid operation.
