"""In-memory test doubles for MongoDB.

The Config API provider only ever calls ``collection.find_one({"doc_type": ...})``,
so a tiny async fake is enough — no need for a real Mongo or ``mongomock``. The
fake client mirrors the ``client[db][collection]`` subscript chain used in
``MongoConfigProvider.__init__`` and ``app/main.py``.
"""
import copy
from typing import Any, Dict, List, Optional


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


class _FakeDatabase:
    def __init__(self, collection: FakeCollection):
        self._collection = collection

    def __getitem__(self, _name: str) -> FakeCollection:
        return self._collection


class FakeMongoClient:
    """Supports the ``client[db_name][collection_name]`` access pattern."""

    def __init__(self, collection: FakeCollection):
        self._database = _FakeDatabase(collection)

    def __getitem__(self, _db_name: str) -> _FakeDatabase:
        return self._database


class FakeApp:
    """Stand-in for the FastAPI app: the poller only touches ``openapi_schema``."""

    def __init__(self):
        self.openapi_schema: Any = "stale-cached-schema"
