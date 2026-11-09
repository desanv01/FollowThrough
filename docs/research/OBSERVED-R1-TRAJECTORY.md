# Observed R1 checkpoint assessment

This is a posthoc assessment of two saved source states from the development R1 episode. It is not a new model run, a formal trial, or a comparison between workflow arms.

| Source state | SHA-256 | Protected functional outcome | Checks |
| --- | --- | --- | --- |
| Prepared R1 initial candidate | `ab554e6153ab92a2b4fe3ddcfa58436ad373a78ebdff318e33dcd1908235ec87` | Fail | 6/8 |
| Actual saved model checkpoint `cp-19.py` | `de04ff314d7d9d8d6961a9ca0420a4d77f9da8755089c6e2cde2d2a5ed0f6d11` | Pass | 8/8 |

Both protected assessments ran in fresh isolated containers using source commit `8fdb226c34e4d8cfdd7e934460a5ba6b855a5d55` and the pinned Python image `python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3`. Each receipt binds the candidate hash, Retry requirements v1, and a fresh assessment run/checkpoint identity. Isolation witnesses were verified, cleanup settled, and separate full-ID Docker readbacks confirmed both owned containers absent. The exclusive runtime lease was released. These assessments made zero provider calls.

The initial candidate failed the `rollback_after_credit` and `retry_after_fault` checks. The saved model checkpoint passed those checks and the six other checks. This establishes observed functional improvement between these two source states. It does not establish exactly when correctness first arose, whether it persisted through unobserved states, or that one workflow arm caused the improvement.

The published [sanitized summary](observed-r1-trajectory.json) uses the existing trajectory summarizer and was independently recomputed from the two protected receipts. It contains one known failure and one known pass, with `cp19` as the first observed correct state and no observed regression. Snapshot counters/times remain null where not independently bound; null does not mean zero.

The original model episode remains **infrastructure_error / excluded** because its public check failed during Docker setup and the historical loop rejected the real check's provenance. Its independently passing protected final grade does not turn that episode into an overall success. The original receipts and historical metadata discrepancies remain preserved.

No pilot, experiment freeze, formal dataset, model-ranking conclusion, or all-scenario qualification is claimed.
