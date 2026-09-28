"""Shared protocol definitions. New model identities belong only in devices/."""
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
KINDS = {"bluez-profile", "rfcomm", "ble-gatt"}
MODEL_TRANSPORT_FIELDS = {"channels", "uuidPreference", "writeHandle", "notifyHandle"}


def contained(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("expected a relative package path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path leaves its package: " + relative)
    return path


def read_json(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(str(path) + ": expected a JSON object")
    return value


def validate_transport(transport):
    if not isinstance(transport, dict) or transport.get("kind") not in KINDS:
        raise ValueError("unknown transport")
    for key in ("replyTimeout", "connectTimeout", "connectDelay", "connectRetry", "connectAttempts", "discoveryTimeout", "registerTimeout"):
        if key in transport and (type(transport[key]) is not int or transport[key] <= 0):
            raise ValueError("transport timing/count must be a positive integer: " + key)
    if transport["kind"] == "bluez-profile":
        ids = transport.get("uuidPreference")
        if not isinstance(ids, list) or not ids or any(not isinstance(u, str) or not UUID.fullmatch(u) for u in ids):
            raise ValueError("profile transport needs UUID preference")
    elif transport["kind"] == "rfcomm":
        channels = transport.get("channels")
        if not isinstance(channels, list) or not channels or any(type(c) is not int or not 1 <= c <= 30 for c in channels):
            raise ValueError("RFCOMM channels must be integers 1-30")
    else:
        for field in ("writeHandle", "notifyHandle"):
            if not isinstance(transport.get(field), str) or not re.fullmatch(r"0x[0-9a-f]{4}", transport[field]):
                raise ValueError("invalid GATT handle")
        if transport.get("addressField", "bleAddress") != "bleAddress":
            raise ValueError("GATT uses the observed BLE address")


def validate_parameters(row, parameters):
    if not isinstance(parameters, dict):
        raise ValueError("model parameters must be an object")
    schema = row.get("parameterSchema", {})
    if set(parameters) - set(schema):
        raise ValueError("undeclared model parameter")
    types = {"boolean": bool, "integer": int, "string": str, "array": list}
    for key, spec in schema.items():
        if spec.get("type") not in types:
            raise ValueError("unsupported parameter schema type")
        if spec.get("required") and key not in parameters:
            raise ValueError("missing model parameter: " + key)
        if key in parameters:
            value = parameters[key]
            if type(value) is not types[spec["type"]]:
                raise ValueError("invalid model parameter: " + key)
            if "values" in spec and value not in spec["values"]:
                raise ValueError("unsupported model parameter value: " + key)
            if ("min" in spec and value < spec["min"]) or ("max" in spec and value > spec["max"]):
                raise ValueError("model parameter out of range: " + key)


def descriptors(root=ROOT, drafts=False):
    rows = []
    for path in sorted((root / "adapters").glob("*/adapter.json")):
        row = read_json(path)
        if row.get("status") == "draft" and not drafts:
            continue
        if type(row.get("apiVersion")) is not int or row["apiVersion"] != 1:
            raise ValueError("unsupported adapter API version")
        if row.get("id") != path.parent.name or not ID.fullmatch(row["id"]):
            raise ValueError("adapter id must equal its directory")
        if row.get("status") not in ("active", "draft"):
            raise ValueError("invalid adapter status")
        if list((path.parent / "models").glob("*.json")):
            raise ValueError("model definitions belong in devices/, not adapters/")
        if row.get("entry"):
            if not contained(path.parent, row["entry"]).is_file():
                raise ValueError("adapter entry does not exist")
            validate_transport(row.get("transport", {}))
        elif not row.get("legacy"):
            raise ValueError("adapter needs a protocol entry")
        if set(row.get("modelTransportFields", [])) - MODEL_TRANSPORT_FIELDS:
            raise ValueError("unsupported model transport field")
        for params in (row.get("unknownModel", {}), row.get("namelessModel", {})):
            validate_parameters(row, params)
        if row.get("legacy"):
            if not contained(root, row["legacy"]["bridge"]).is_file():
                raise ValueError("legacy bridge does not exist")
            if type(row.get("priority")) is not int:
                raise ValueError("legacy priority is required")
        rows.append(row)
    legacy = [r for r in rows if r.get("legacy")]
    if len({r["priority"] for r in legacy}) != len(legacy):
        raise ValueError("legacy routing priorities must be unique")
    return sorted(rows, key=lambda r: (r.get("priority", 9999), r["id"]))


def get_adapter(adapter_id, root=ROOT):
    return next((row for row in descriptors(root) if row["id"] == adapter_id), None)


def select(uuids, ble_address="", root=ROOT):
    ids = [u.strip().lower() for u in uuids]
    for row in descriptors(root):
        if not row.get("legacy"):
            continue
        match = row["match"]
        if match.get("ble") and ble_address or any(u in match.get("uuids", []) or match.get("uuidPrefix") and u.startswith(match["uuidPrefix"]) for u in ids):
            return row["id"]
    return ""


def reference_model(row, context):
    context = dict(context)
    if row['id'] == 'nothing':
        context['name'] = context.get('name', '').strip().casefold().removeprefix('nothing ')
    if row['id'] == 'soundcore':
        context['uuid'] = next((u.lower() for u in context.get('uuids', [])
                                if u.lower().startswith(row['match']['uuidPrefix'])), context.get('uuid', '').lower())
    return next((ref for ref in row.get('referenceModels', {}).values()
                 if all(context.get(k) == v for k, v in ref['match'].items())), None)


def model_parameters(row, context, root=ROOT):
    # Private migration references for replaying frozen old pins. New packages
    # are resolved explicitly by devices.resolve, never by a model fallback.
    ref = reference_model(row, context)
    if ref:
        return ref['parameters']
    return row.get("namelessModel", row.get("unknownModel", {})) if not context.get("name") else row.get("unknownModel", {})


def transport_for(row, context, root=ROOT):
    transport = dict(row['transport'])
    ref = reference_model(row, context)
    if ref:
        transport.update(ref.get('transport', {}))
    elif not context.get('name', '').strip():
        transport.update(row.get('namelessTransport', {}))
    if row['id'] == 'soundcore':
        uuid = next((u.lower() for u in context.get('uuids', [])
                     if u.lower().startswith(row['match']['uuidPrefix'])), context.get('uuid', '').lower())
        if uuid.startswith(row['match']['uuidPrefix']) and UUID.fullmatch(uuid):
            transport['uuidPreference'] = [uuid]
    validate_transport(transport)
    return transport


def instantiate(path, parameters):
    spec = importlib.util.spec_from_file_location("omaphones_protocol_" + path.parent.name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from omaphones.api import Protocol
    adapter = module.Adapter(parameters)
    if not isinstance(adapter, Protocol):
        raise ValueError("adapter must implement the shared Protocol API")
    return adapter


def load_protocol(row, context, root=ROOT):
    return instantiate(contained(root / "adapters" / row["id"], row["entry"]), model_parameters(row, context, root))


def shell_rows(root=ROOT):
    from omaphones.devices import packages, resolve
    rows = []
    for directory, profile in packages(root):
        definition = resolve(profile, directory, root)
        rows.append({"name": "device:" + profile["id"], "profile": profile["id"], "deviceMatch": profile["match"],
                     "bridge": "omaphones-device", "args": [], "runtime": True, "controls": {},
                     "needsBleAddress": definition["transport"]["kind"] == "ble-gatt", "supportCache": "", "batterySource": profile.get("batterySource", "none"),
                     "uuidPreference": definition["transport"].get("uuidPreference", [])})
    for row in descriptors(root):
        legacy = row.get("legacy")
        if not legacy:
            continue
        item = {"name": row["id"], "bridge": legacy["bridge"], **row["match"], "args": legacy["args"],
                "runtime": False, "controls": legacy.get("controls", {}),
                "needsBleAddress": bool(row["match"].get("ble")), "supportCache": row.get("supportCache", ""),
                "uuidPreference": row["match"].get("uuids", [])}
        if legacy.get("ambient"):
            item["ambient"] = legacy["ambient"]
        for key in ("extraModes", "modeLabels"):
            if legacy.get(key):
                item[key] = legacy[key]
        rows.append(item)
    return rows
