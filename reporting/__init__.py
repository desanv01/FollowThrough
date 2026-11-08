"""FollowThrough offline reporting projection package (contract v0.1).

Public API (resolved lazily so ``python -m reporting.report`` does not warn
about a pre-imported submodule):
    build_report(data) -> dict
    render_markdown(report) -> str
    generate(input_path, output_dir) -> dict
    main(argv=None) -> int

Importing this package performs no file, network or other I/O; only the
standard library is imported when an attribute is first requested.
"""

__all__ = [
    "ReportError",
    "build_report",
    "generate",
    "main",
    "render_markdown",
]


def __getattr__(name):
    if name in __all__:
        from . import report as _report

        return getattr(_report, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
