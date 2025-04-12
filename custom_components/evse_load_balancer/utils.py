"""Utilities."""

from collections.abc import Callable


def combined_conf_key(*conf_keys: list) -> str:
    """Combine configuration keys into a single string."""
    return ".".join(conf_keys)


def get_callable_name(obj: Callable) -> str:
    """Get the name as string of a callable object."""
    if isinstance(obj, property):
        return obj.fget.__name__
    return obj.__name__
