from collections.abc import Callable
from functools import partial, update_wrapper
from typing import Any


def bind[T](func: Callable[..., T], /, **dependencies: Any) -> Callable[..., T]:
    # ToolNode reads the tool function's type hints to find injected arguments; a bare partial
    # has none, so the wrapped function's metadata is copied onto it.
    return update_wrapper(partial(func, **dependencies), func)
