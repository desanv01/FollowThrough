# Shared check-port contract v0.1

Main-owned seam allowing P2 executor and P3 episode-loop implementation to proceed independently. Neither worker edits this contract or implements the other worker's component.

P3 receives a trusted injected callable check_port(request: dict) -> dict. Exact request keys: request_id (ASCII identifier), workspace (absolute validated agent directory; host-only), candidate_filename (processor.py or preview.py), candidate_sha256 (actual lowercase64hex), requirements_version (v1 or v2). No command, image, executable, shell, environment or mount override exists. P3 serializes checks with edits and hashes current source before dispatch.

Exact response keys: schema_version="check-port-0.1", request_id, evidence_kind="synthetic_check" or "real_container_check", candidate_sha256, requirements_version, status, exit_code, stdout, stderr, cleanup.
status: passed, failed, timeout, output_limit, cancelled, infrastructure_error.
exit_code: integer excluding bool or null. passed requires0; failed requires nonzero integer. Others may retain observed exit or null.
stdout/stderr: UTF8 text, <=32768 bytes each. No host/private metadata in these fields; data is untrusted agent-visible checker output.
cleanup: settled or unknown. Unknown forbids passed/failed.
Identity fields must equal the issued request. Mismatch, invalid keys/types/schema, excessive output or exception means infrastructure failure, never a check pass. P3 retains safe bounded diagnostic metadata, not raw exceptions or extra payload fields. Never promote stdout claims to functional grade.

P3 r1 accepts only synthetic_check in synthetic development episodes. A later qualified live bridge may admit real_container_check with actual evidence. Fake/model-provided receipts are not upgraded by changing a label.

P2 independently implements container-check-0.1 host receipts. A future Main-owned bridge maps:
check_passed -> passed; check_failed -> failed; timeout/output_limit/cancelled retain category; refused/infrastructure_error -> infrastructure_error.
Container cleanup must be verified/not-created appropriately, all process settlement known, and source hashes matched. It strips host paths, Docker command metadata and protected records from the agent-visible projection. A production label means actually invoked Docker, not qualified isolation; later qualification is separately required.

P2 does not import P3; P3 does not import candidate Rust files. Raw P2 receipts and P4 reporting projections are separate schemas. Independent protected grading later provides semantic outcomes. Public checker exit0 is not hidden correctness, workflow completion or permission to skip review.

