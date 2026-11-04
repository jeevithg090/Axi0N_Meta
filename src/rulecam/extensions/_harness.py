"""The child side of the extension sandbox: run one validated `check` over a batch of samples.

`sandbox.run_check` starts this file as `python -I -S _harness.py` with an empty environment, in
an empty temporary directory. It is deliberately standalone: no Rulecam import (with `-S` the
package is not even importable), only `json`, `math`, `resource` and `sys` from the standard
library.

Protocol: one JSON object on stdin, `{"code": str, "samples": [...], "limits": {...}}`; one JSON
object on stdout, `{"results": [{"ok": true, "result": ...} | {"ok": false, "error": str}]}` or
`{"fatal": str}`. The process limits are set before the model's code is even compiled.
"""

from __future__ import annotations

import contextlib
import json
import math
import resource
import sys

_MB = 1024 * 1024
_ERROR_CHARS = 200
_SAFE_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "int",
    "isinstance",
    "len",
    "list",
    "max",
    "min",
    "range",
    "round",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "True",
    "False",
    "None",
)
_SAFE_EXCEPTIONS = (
    "ArithmeticError",
    "Exception",
    "IndexError",
    "KeyError",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
)


def _limit(kind: int, value: int) -> None:
    """Lower one resource limit, keeping the hard limit no higher than before."""
    try:
        resource.setrlimit(kind, (value, value))
    except (ValueError, OSError):
        _, hard = resource.getrlimit(kind)
        if hard == resource.RLIM_INFINITY or value <= hard:
            resource.setrlimit(kind, (value, hard))


def apply_limits(limits: dict[str, int]) -> None:
    """Cap CPU, memory, open files, file size and processes for this process.

    Args:
        limits: `cpu_s`, `memory_mb` and `max_files` from the parent.
    """
    _limit(resource.RLIMIT_CPU, int(limits["cpu_s"]))
    if sys.platform == "darwin":
        # macOS reserves a huge address space at start-up and does not enforce RLIMIT_AS, so
        # lowering it fails; Linux (the API pod, CI) enforces it.
        with contextlib.suppress(ValueError, OSError):
            _limit(resource.RLIMIT_AS, int(limits["memory_mb"]) * _MB)
    else:
        _limit(resource.RLIMIT_AS, int(limits["memory_mb"]) * _MB)
    _limit(resource.RLIMIT_FSIZE, 0)
    _limit(resource.RLIMIT_NOFILE, int(limits["max_files"]))
    with contextlib.suppress(ValueError, OSError):
        _limit(resource.RLIMIT_NPROC, 0)  # best effort: root ignores it


def _only_math(
    name: str,
    globals_: object = None,
    locals_: object = None,
    fromlist: object = (),
    level: int = 0,
) -> object:
    """The only importer the code gets: `import math`, nothing else."""
    del globals_, locals_, fromlist
    if name == "math" and level == 0:
        return math
    msg = f"import of {name!r} is not allowed"
    raise ImportError(msg)


def safe_builtins() -> dict[str, object]:
    """The builtins the model's code may use (the validator allows the same names)."""
    source = vars(__builtins__) if not isinstance(__builtins__, dict) else __builtins__
    names = _SAFE_NAMES + _SAFE_EXCEPTIONS + ("ImportError",)
    table: dict[str, object] = {name: source[name] for name in names}
    table["__import__"] = _only_math
    return table


def run(code: str, samples: list[object]) -> list[dict[str, object]]:
    """Compile `code` with the safe builtins and call its `check` on every sample.

    Args:
        code: The validated module source.
        samples: JSON samples.

    Returns:
        One entry per sample: the result, or the exception's type and message.
    """
    scope: dict[str, object] = {"__builtins__": safe_builtins(), "__name__": "extension"}
    exec(compile(code, "<extension>", "exec"), scope)  # noqa: S102 - the sandbox's whole purpose
    check = scope["check"]
    if not callable(check):
        msg = "check is not a function"
        raise TypeError(msg)
    out: list[dict[str, object]] = []
    for sample in samples:
        try:
            out.append({"ok": True, "result": check(sample)})
        except Exception as exc:  # every failure is reported per sample, none escapes
            out.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]})
    return out


def main() -> int:
    """Read the request, run it under the limits and write one JSON answer."""
    payload = json.loads(sys.stdin.read())
    limits = payload["limits"]
    apply_limits(limits)
    try:
        answer: dict[str, object] = {"results": run(payload["code"], payload["samples"])}
        text = json.dumps(answer, allow_nan=False)
    except (MemoryError, RecursionError, ValueError, TypeError, SyntaxError) as exc:
        text = json.dumps({"fatal": f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]})
    if len(text) > int(limits["max_output_bytes"]):
        text = json.dumps({"fatal": f"output too large ({len(text)} bytes)"})
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
