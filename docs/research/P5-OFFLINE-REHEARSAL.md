# P5 offline rehearsal — clean source 45374ce

**Status: offline reproduction rehearsal. Synthetic fixture and trusted authored
controls only. No formal result, provider call or model evidence.**

## Identity

| Field | Value |
|---|---|
| Source archive | `source-45374ce.zip` (Main-held seed) |
| ZIP SHA-256 | `b1fbdc53f405991d769016c6dfed77e1d71d17ba44671e61c98ff6bf59200520` |
| ZIP bytes | 425,574 |
| ZIP comment | `45374ce6dfa9212a44cae495a26a2bf81ed0bd78` |
| Single prefix | `FollowThrough-45374ce6dfa9212a44cae495a26a2bf81ed0bd78/` |
| Entries / files | 147 / 115 |
| Extracted bytes | 1,369,547 |
| Tree manifest SHA-256 | `75b5869e234cb7614bd48941543c28c315765d1ddb3514ec6dc3427ee829bd45` |

Validation refused no entries: the archive had a single matching prefix and no
traversal, absolute/drive, duplicate, symlink or special-file entries. Extraction
occurred only into new owned scratch; the extracted tree matched both the archive
inventory and the independently generated file manifest. The 115-entry file
manifest is in `p5/rehearsal/source-45374ce-manifest.json`.

## Checks performed from the clean source root

| Check | Result |
|---|---|
| `python -B -m reporting.report --input tests/fixtures/reporting/v0.1/input.json --output-dir <new>` | exit 0; 8 episodes, 7 groups; inventory and all strict outcomes match the fixture expectation; every expected nested group subset matches; synthetic warning present |
| `python -B grader/retry_processing.py --self-test` (direct) | **exit 1** — `PermissionError` at `TemporaryDirectory` setup in the managed sandbox (infrastructure, not fixture failure) |
| retry self-test via documented temp-mode shim | exit 0; correct candidate passes, original and mark-first fail exactly their expected four checks; all three `matched_expectation=true` |
| `python -B grader/preview_cache.py --self-test` via the same shim | exit 0; `self_test_passed=true`, 3 controls, 13 checks; correct passes, original fails exactly four expected, v1 fails exactly two |

The temp-mode shim changes only `tempfile.mkdtemp` creation mode; grader source is
unmodified. Direct-command incompatibility is tracked as **P5-ISS-001**.

## Limits and missing formal package evidence

- The GitHub source archive is not a frozen release/campaign package.
- No formal dataset, protected grade/checkpoint receipts, `real_provider` rows,
  model identity, usage or billing reconciliation exist.
- No accepted reporting/bridge revision is bound into a package manifest.
- No sanitized formal traces exist.
- This rehearsal therefore supports P5.3.1/P5.3.2 **source-side** evidence only;
  formal package reproduction and P5.2/P5.3 results remain pending.
