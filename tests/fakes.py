"""In-memory test doubles for MongoDB.

The Config API provider reads each governing collection with ``find_one({})``, so
a tiny async fake is enough — no need for a real Mongo or ``mongomock``. The fake
client mirrors the ``client[db][collection]`` subscript chain used in
``MongoConfigProvider.__init__`` and ``app/main.py``, routing each collection name
to its own ``FakeCollection``.
"""
import copy
from typing import Any, Dict, Iterable, List, Optional


class FakeCollection:
    """Minimal async stand-in for a Motor/pymongo async collection."""

    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        # Stored by deep-copy so seed fixtures can't be mutated through the fake.
        self._docs: List[Dict[str, Any]] = [copy.deepcopy(d) for d in (docs or [])]
        self.find_one_calls = 0

    async def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.find_one_calls += 1
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                # Hand back a copy so callers can't mutate our stored state.
                return copy.deepcopy(doc)
        return None

    async def update_one(
        self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False
    ) -> "FakeUpdateResult":
        """Supports only ``$addToSet`` — the single operator the provider uses."""
        field, value = next(iter(update["$addToSet"].items()))
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                values = doc.setdefault(field, [])
                if value in values:
                    return FakeUpdateResult(1, 0, None)
                values.append(value)
                return FakeUpdateResult(1, 1, None)
        if not upsert:
            return FakeUpdateResult(0, 0, None)
        self._docs.append({**query, field: [value]})
        return FakeUpdateResult(0, 0, "fake-upserted-id")


class CallCounter:
    """Aggregates ``find_one_calls`` across collections so cache assertions can
    count total Mongo hits regardless of which collection served them."""

    def __init__(self, collections: Iterable[FakeCollection]):
        self._collections = list(collections)

    @property
    def find_one_calls(self) -> int:
        return sum(c.find_one_calls for c in self._collections)


class _FakeDatabase:
    def __init__(self, collections: Dict[str, FakeCollection]):
        self._collections = collections

    def __getitem__(self, name: str) -> FakeCollection:
        # Unknown collections behave as empty (mirrors a not-yet-seeded Mongo).
        return self._collections.setdefault(name, FakeCollection([]))


class FakeMongoClient:
    """Supports the ``client[db_name][collection_name]`` access pattern, routing
    each collection name to its own ``FakeCollection``."""

    def __init__(self, collections: Dict[str, FakeCollection]):
        self._database = _FakeDatabase(collections)

    def __getitem__(self, _db_name: str) -> _FakeDatabase:
        return self._database


class FakeApp:
    """Stand-in for the FastAPI app: the poller only touches ``openapi_schema``."""

    def __init__(self):
        self.openapi_schema: Any = "stale-cached-schema"


class FakeUpdateResult:
    """Mirrors the fields of a pymongo ``UpdateResult`` the provider reads."""

    def __init__(self, matched_count: int, modified_count: int, upserted_id: Optional[str]):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id
