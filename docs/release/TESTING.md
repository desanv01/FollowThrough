# Setup and judging walkthrough — micro1 Eval Hackathon 2026

## Recorded-results mode: free and offline

Use Python3.11+ from a clean extracted source ZIP or Git checkout. This path uses the standard library; no provider key, Docker or paid credits are needed. Run from the directory containing README.md:

```powershell
python -B -m reporting.run_ledger --input docs/research/recorded-development-input.json --format tui
```

Use `j/k`, arrows, `Enter`, `/`, `Esc` and `q` as described in README. For a non-interactive terminal use `--format text` or `--format json`. The input has three real-provider records (two development, one incomplete pilot), all excluded. It is a saved inspection path, not fresh inference.

## Reproduce the original authored controls

```powershell
python -B grader/retry_processing.py --self-test
python -B grader/preview_cache.py --self-test
```

The trusted correct variants pass and the intentional faulty variants fail their documented checks. Both commands passed in a clean native source extraction and in hosted Ubuntu/Windows CI. They are executable original graders, not model benchmark scores. No third-party Python package installation is needed.

## Build the portable report

Use fresh output paths:

```powershell
python -B -m reporting.report --input docs/research/recorded-development-input.json --output-dir runs/judging-report
python -B -m reporting.viewer --report runs/judging-report/report.json --output runs/judging-report/report.html
```

Open `runs/judging-report/report.html` locally. The generated page has no scripts, login, API execution or network dependency. [Recorded output artifacts](../research/recorded-outputs/README.md) include actual parsed model actions, bound candidates, raw public-check streams and model-alias facts; the exact raw provider/rejected payload boundary is documented there.

## Fresh bounded execution

The [evaluation guide](../project/QUALIFIED-EVALUATION.md) describes separate clean source/trusted storage, setup draft, canonical ledger, credential loader and operation gate. [Python public checking](../project/PYTHON-CHECK.md) documents the qualified backend; Docker Desktop Linux containers and the exact supported local image are required. Building the Rust source uses the pinned compatible toolchain: `cargo build --locked --manifest-path runner/Cargo.toml`.

Fresh inference incurs provider charges and needs explicit bounded operation authorization. The included recorded mode incurs none. Reservations are not invoices. The Rust child Docker-info failure on the recorded host remains separate from the qualified Python backend. No complete formal comparison or coding-capability failure is inferred from the three retained excluded attempts.

## Provenance and previous work

The Retry/Cache fixtures are original project work created for this evaluation. No previous implementation or employer/client data was copied into them. Development used Codex and human-launched DeepSeek harness workers with separate review; [contributions](CONTRIBUTIONS.md) retains that attribution. The repository presentation follows the owner's VerifForge layout, after the earlier ErrataScope reference, with original FollowThrough content and actual FollowThrough captures; no reference project's product claims or task data were copied.

Project domain is software maintenance, transactional data integrity, cache correctness and agent workflow verification; no employment tenure or professional credential is claimed. Original project work is MIT licensed; dependencies retain their terms. The public walkthrough's final card reflects the earlier AGPL recording snapshot; current source is MIT.
