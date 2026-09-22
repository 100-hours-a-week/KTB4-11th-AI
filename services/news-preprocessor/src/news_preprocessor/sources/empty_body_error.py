"""Raised when an article page yields no body text."""


class EmptyBodyError(Exception):
    """The page had no body text — almost always a change in the site's markup."""
