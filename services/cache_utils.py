from typing import Any, Callable, Dict, List, TypeVar

T = TypeVar("T")


def bounded_cache_get(
    store: Dict[Any, T],
    order: List[Any],
    key: Any,
    factory: Callable[[], T],
    max_entries: int = 64,
) -> T:
    if key in store:
        return store[key]
    value = factory()
    store[key] = value
    order.append(key)
    while len(order) > max_entries:
        old = order.pop(0)
        store.pop(old, None)
    return value
