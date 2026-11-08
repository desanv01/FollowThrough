"""FollowThrough scenario preparation package.

Importing this package performs no I/O. The implementation lives in
scenarios.prepare so that ``python -B -m scenarios.prepare`` remains usable
without installation.
"""

__all__ = ["prepare_scenario"]


def prepare_scenario(project_root, scenario_id, output_dir):
    """Prepare one deterministic scenario package (lazy import, no import-time I/O)."""
    from .prepare import prepare_scenario as _prepare_scenario

    return _prepare_scenario(project_root, scenario_id, output_dir)
