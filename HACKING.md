# HACKING.md

## Project

`asyncvarlink` is a pure‑Python, `asyncio`‑based implementation of the
[varlink](https://varlink.org) IPC protocol. Three design choices differentiate
it from the reference implementation.

- Based on asyncio
- varlink interface descriptions are derived from Python type annotations.
  Interface descriptions are produced rather than consumed.
- Even though file descriptor passing is out of scope in principle, it is
  supported with semantics similar to systemd.

## Toolchain

Install development tools using: `pip install -e ".[devel,test]"`.

- Python `>=3.11`.
- Fully type-annotated.
- Build backend: `flit_core` via `pyproject.toml`
- Consult the `justfile` or run `just -l` to see common tasks.

Code changes must pass `pytest` and `mypy --strict asyncvarlink`.

## Layout

- `asyncvarlink/` — the package (public API re‑exported in `__init__.py`)
- `tests/` — unit test suite (uses `unittest`, `IsolatedAsyncioTestCase` via
  `StrictAsyncioTestCase` and `hypothesis`)
- `docs/` — Sphinx sources

## Architecture and file descriptor lifetime

Refer to `docs/structure.rst`.

## Conventions

- **Style:** `black`; `pylint` enabled (`good-names = ["fd"]`). Maximum line
  length for code is 79.
- **Typing:** `mypy --strict` with `explicit-override`. Use `typing.override`
  (the `asyncvarlink/types.py` shim covers 3.11 until support is dropped).
  Prefer `collections.abc` generics.
- **Docstrings:** every public class/function has a docstring. Methods
  decorated with `@varlinkmethod` expose their docstrings via introspection.
- **Headers:** Every source file must have a `SPDX-License-Identifier`.
- **API**: Most of the API is reexported from `asyncvarlink/__init__.py`.
