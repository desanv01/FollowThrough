# Retained model and checker outputs

This bundle contains actual saved outputs for the two later DeepSeek-backed attempts. It is separate from authored fixtures and from the normalized report projection. Every attempt remains excluded after its provider/protocol stop; independent functional passes are not overall episode successes.

- `parsed-model-outputs.json` preserves each successful bridge response object (action, usage and requested/returned model alias) without changing that object. It adds the recorded role/index and selected usage observation. Private requests, role histories, credentials and hidden reasoning are not included. The final failed response is null with its actually recorded `bridge_invalid_content` code.
- `processor.py` is the exact saved candidate whose SHA256 agrees with the protected functional grade. It is included as evidence, not automatically executed by the report viewers.
- `public-check-N.stdout.txt` and `.stderr.txt` are byte-identical actual public-check streams. `functional-grade.json` is an explicitly selected safe projection of the independent protected grade; it is not the raw private executor receipt.
- `MANIFEST.json` records file hashes, candidate binding, available response counts and actual requested/returned alias `deepseek-flash`. That alias does not establish an immutable provider model version.

Exact provider wire responses and the rejected final raw payloads were not retained. They cannot be reconstructed from these parsed actions. The earlier historical R1 private transcript is outside this bundle. These boundaries remain explicit so raw public-check/candidate bytes are not confused with a complete raw model-wire archive.

Use the root README to run the graders or inspect the three saved report rows. The candidate evidence is untrusted evaluated output; fresh candidate execution belongs in the isolated runtime. No additional evaluation was performed to assemble this record.
