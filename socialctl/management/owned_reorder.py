"""Non-atomic playlist reordering: persist every intent/result before the next move."""
import copy
import json

from socialctl.management.resource_changes import _event, digest
from socialctl.management.owned_changes import complete, ordered, readback, identity
from socialctl.management.owned_schema import OwnedEdit
from socialctl.management.youtube_resources import ResourceError, ResourceRejected, ResourceConflict, ResourceUncertain


def semantic(rows):
    return [{k: v for k, v in row.items() if k != "etag"} for row in ordered(rows)]


def apply_order(client, store, change, edit, approval_digest):
    expected = ordered(change.before["collection"])
    change.status = "applying"
    _event(change, "order_intent", approval_digest=approval_digest, atomic=False)
    store.save(change)
    for plan in change.plan:
        try:
            identity(client, change)
            current = ordered(complete(client.list_items(edit.playlist_id)))
            if current != expected:
                raise ResourceConflict("conflict: lista cambió antes del siguiente movimiento")
            row = next(r for r in current if r["id"] == plan["item_id"])
            step = {"item_id": row["id"], "status": "applying", "etag": row["etag"], "result_id": None}
            change.steps.append(step)
            _event(change, "write_intent", item_id=row["id"], approval_digest=approval_digest)
            store.save(change)
            move = OwnedEdit(action="item-update", playlist_id=edit.playlist_id, item_id=row["id"], patch={"snippet": {"position": plan["body"]["snippet"]["position"]}})
            response = client.effect(move, "playlistItems", row["id"], plan["body"], etag=row["etag"])
            step["result_id"] = response.get("id")
            step["status"] = "uncertain"
            _event(change, "write_response", item_id=row["id"], result_id=step["result_id"])
            store.save(change)
            if step["result_id"] != row["id"]:
                raise ResourceUncertain("reorder devolvió un playlistItemId inesperado")
            predicted = copy.deepcopy(current)
            moved = next(r for r in predicted if r["id"] == row["id"])
            predicted.remove(moved)
            predicted.insert(plan["body"]["snippet"]["position"], moved)
            for index, other in enumerate(predicted):
                other["snippet"]["position"] = index
            observed = ordered(complete(client.list_items(edit.playlist_id)))
            if semantic(observed) != semantic(predicted):
                raise ResourceUncertain("readback de orden no coincide o cambió otro campo")
            step["status"] = "verified"
            step["readback"] = next(r for r in observed if r["id"] == row["id"])
            step["collection_digest"] = digest(json.dumps(observed, sort_keys=True, separators=(",", ":")).encode())
            _event(change, "move_verified", item_id=row["id"])
            store.save(change)
            expected = observed
        except ResourceError as exc:
            if change.steps and change.steps[-1]["status"] in {"applying", "uncertain"}:
                change.steps[-1]["status"] = ("conflict" if isinstance(exc, ResourceConflict) else "failed") if isinstance(exc, ResourceRejected) else "uncertain"
            change.status = "partial" if any(s["status"] == "verified" for s in change.steps) else ("failed" if isinstance(exc, ResourceRejected) else "uncertain")
            _event(change, "order_stopped", error=str(exc), atomic=False)
            store.save(change)
            return change
    change.status = "uncertain"
    return readback(client, store, change)
