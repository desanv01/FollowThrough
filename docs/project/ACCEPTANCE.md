# Acceptance gates

Each workstream reports implemented / integrated / independently reviewed / actual acceptance separately.

## Retry fixture
- SPEC states identity, input validity, collision semantics, transaction failures, acknowledgement loss and batch behavior.
- Correct reference passes all specified cases; original and mark-first candidates fail expected behavioral checks.
- Grade output names actual candidate path/hash and per-requirement outcomes. Execution errors are not indistinguishable from functional failures.
- Only trusted fixtures run locally at this stage; importing Python candidates is not a security boundary.

## Runner foundation
- Creates new owned run directory; refuses overwrite and invalid manifest paths.
- Writes ordered JSONL events, source hashes, actual subprocess outcome and honest evidence label.
- Local control run can invoke retry grader and return distinct infrastructure versus functional status.
- Bounds subprocess output and runtime. Reports cancellation/cleanup limitations truthfully.

## Live milestone
- Documented official DeepSeek request/tool continuation, real model identity and usage where exposed.
- Per-operation budget/access agreed; credentials outside task workspace and logs.
- Model can actually inspect/edit/check task through tools; no scripted reference solution presented as inference.
- Grader protected by verified isolation before untrusted candidates run. Independent review required.

## Release
- Frozen scenarios/prompts/models/settings/checker and all attempt dispositions.
- Sanitized traces and actual results; method limitations and AI contribution record.
- ZIP under 50 MB with root README; <=5-minute accessible video and description per previously reviewed rules.
