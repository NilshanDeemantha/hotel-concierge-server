"""A small in-memory Firestore for the tests. Transactions behave like Firestore's: a commit is refused (Aborted) if a
document the transaction read has changed since, and `retrying` runs the function again, as firestore.transactional does."""

from google.api_core.exceptions import Aborted
from firebase_admin import firestore


class Snap:
    def __init__(self, id, data):
        self.id, self._data, self.exists = id, data, data is not None

    def to_dict(self):
        return None if self._data is None else {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._data.items()}


class Store:
    def __init__(self):
        self.docs, self.versions, self.before_commit = {}, {}, None

    def put(self, path, data):
        self.docs[path] = data
        self.versions[path] = self.versions.get(path, 0) + 1


class Ref:
    def __init__(self, store, path):
        self.store, self.path, self.id = store, path, path.split("/")[1]

    def get(self, transaction=None):
        if transaction is not None:
            transaction.reads[self.path] = self.store.versions.get(self.path, 0)
        return Snap(self.id, self.store.docs.get(self.path))

    def set(self, data, merge=False):
        self.store.put(self.path, {**(self.store.docs.get(self.path) or {}), **data} if merge else data)

    def update(self, data):
        self.store.put(self.path, {**(self.store.docs[self.path]), **data})


class Query:
    def __init__(self, store, name, rows=None):
        self.store, self.name, self.rows = store, name, rows

    def _all(self):
        if self.rows is not None:
            return self.rows
        return [Snap(p.split("/")[1], d) for p, d in self.store.docs.items() if p.startswith(self.name + "/")]

    def where(self, filter):
        keep = {"==": lambda a, b: a == b, "in": lambda a, b: a in b}[filter.op_string]
        return Query(self.store, self.name, [s for s in self._all() if keep(s._data.get(filter.field_path), filter.value)])

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._all())


class Collection(Query):
    def document(self, id):
        return Ref(self.store, f"{self.name}/{id}")


class Tx:
    def __init__(self, store):
        self.store, self.reads, self.writes = store, {}, []

    def set(self, ref, data, merge=False):
        self.writes.append((ref.path, data, merge))

    def update(self, ref, data):
        self.writes.append((ref.path, data, "update"))

    def commit(self):
        if self.store.before_commit:
            hook, self.store.before_commit = self.store.before_commit, None
            hook()  # another guest's booking lands while this one is still in flight
        if any(self.store.versions.get(p, 0) != v for p, v in self.reads.items()):
            raise Aborted("contention")
        for path, data, merge in self.writes:
            current = dict(self.store.docs.get(path) or {})
            if merge == "update":
                data = {**current, **data}
            elif merge:
                days = {**current.get("days", {}), **data.get("days", {})}
                days = {k: v for k, v in days.items() if v is not firestore.DELETE_FIELD}
                data = {**current, **data, "days": days}
            self.store.put(path, data)


class Db:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return Collection(self.store, name)

    def transaction(self):
        return Tx(self.store)


def retrying(fn):
    """What firestore.transactional does: run again from the start if the commit is aborted."""
    def run(tx, *args):
        for _ in range(5):
            tx = Tx(tx.store)
            try:
                result = fn(tx, *args)
                tx.commit()
                return result
            except Aborted:
                continue
        raise Aborted("gave up")
    return run


