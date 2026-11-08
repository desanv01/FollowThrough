# Reporting v0.1 numeric clarification for P4-REPORT/r2

Main-owned clarification, approved for this finite correction on 7 November 2026 before reporting source acceptance or live use. This document supersedes only the unconstrained upper bounds for trial and trace seq in REPORTING-CONTRACT.md; all other semantics and exact fields remain unchanged. Preserve the original contract and r1 evidence.

Both trial and trace seq must be Python integers excluding bool in the inclusive range 1 through 9,007,199,254,740,991 (2^53-1), matching the existing resource integer ceiling. Trace ordering still requires strictly increasing seq with gaps allowed. Both the parsed-object API and JSON CLI must reject larger values with a stable, bounded input error. Do not change global interpreter digit limits. JSON numeric conversion failures, including excessively long integer literals, must become a controlled input error without raw values or a traceback. Resource values retain their existing null/nonnegative integer rules.

Tests must cover the maximum accepted value, maximum plus one, bool/zero/negative refusal, and a greater-than-4300-digit integer through the real CLI and equivalent parsed-object API. These are synthetic fixtures. No fixture outcomes or denominator semantics change.
