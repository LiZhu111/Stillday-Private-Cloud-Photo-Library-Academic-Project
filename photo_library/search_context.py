"""Search progress and a bounded execution deadline, scoped to one worker."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_execution = ContextVar('semantic_search_execution', default=None)


@contextmanager
def execution(callback, deadline):
    token = _execution.set((callback, deadline))
    try:
        yield
    finally:
        _execution.reset(token)


def report(stage):
    context = _execution.get()
    if context:
        callback, deadline = context
        if time.time() >= deadline:
            raise TimeoutError('The search exceeded its total time limit')
        callback(stage)
