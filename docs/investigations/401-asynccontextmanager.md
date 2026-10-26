# Market subscription decorator diagnostic (#401)

The warning comes from the deprecated `AsyncIterator` overload in the installed
Pylance 2026.4.1 fallback typeshed, not from deprecation of the standard-library
`contextlib.asynccontextmanager` decorator. Its `stdlib/contextlib.pyi:110–117`
provides a supported `AsyncGenerator` overload followed by a deprecated
`AsyncIterator` overload.

Reproduced with Pyright 1.1.414, Pylance 2026.4.1's bundled typeshed, Python target
3.13 on Linux, and this worktree's `.venv/bin/python` (CPython 3.13.12).
The original editor's selected interpreter and live diagnostics were not available;
this reproduction explicitly selected the project interpreter.

With `reportDeprecated` enabled, the full diagnostic at
`market_subscription.py:91:6` was:

```text
The function "asynccontextmanager" is deprecated
  Annotating the return type as `-> AsyncIterator[Foo]` with `@asynccontextmanager` is deprecated. Use `-> AsyncGenerator[Foo]` instead. (reportDeprecated)
```

To reproduce, point Pyright's `typeshedPath` to the installed Pylance extension's
`dist/typeshed-fallback`, set `pythonVersion` to `3.13` and `reportDeprecated` to
`error`, include `services/market-http/src/market_http/market_subscription.py`,
and run Pyright with `--pythonpath .venv/bin/python`. The original annotation
produced one error; `AsyncGenerator[asyncio.Queue[MarketEvent | None]]` produced
zero errors or warnings. Project checker ty 0.0.82 also passes for this module.
Checking the entire market-http source with ty reports eight existing diagnostics
in pool instrumentation and price, QuestDB, and ranking code; the original annotation
produces the same eight diagnostics.

Only the generator's annotation and import change. The decorator, yielded queue,
registration, `finally` cleanup, and price/orderbook callers remain unchanged.
Existing subscription tests cover normal exit and listener failure; an additional
test cancels an active subscriber and verifies both channel registrations are removed.

Python's [contextlib documentation](https://docs.python.org/3.13/library/contextlib.html#contextlib.asynccontextmanager)
continues to support this decorator for asynchronous generator functions.
