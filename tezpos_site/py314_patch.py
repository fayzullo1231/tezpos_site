"""
Workaround for Django 5.1 + Python 3.14 template Context copy bug.

See: https://code.djangoproject.com/ticket/35844
Fixed upstream in Django 5.2+ (commit 8d7b142).

On Python 3.14+, ``super`` objects are copyable, so the old:

    duplicate = super().__copy__()
    duplicate.dicts = self.dicts[:]

sets attributes on a ``super`` proxy and raises AttributeError. Admin pages
that copy RequestContext (changelist, etc.) crash with 500.
"""
from __future__ import annotations

import sys
from copy import copy


def apply_django_py314_patches() -> None:
    if sys.version_info < (3, 14):
        return

    from django.template.context import BaseContext

    if getattr(BaseContext.__copy__, "_tezpos_py314", False):
        return

    def _base_context_copy(self):
        # Match Django 5.2+ BaseContext.__copy__ (refs #35844).
        duplicate = BaseContext()
        duplicate.__class__ = self.__class__
        duplicate.__dict__ = copy(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate

    _base_context_copy._tezpos_py314 = True  # type: ignore[attr-defined]
    BaseContext.__copy__ = _base_context_copy  # type: ignore[method-assign]
