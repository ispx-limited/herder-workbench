#!/usr/bin/env python3
"""The parity gate: is a model fully onboarded, or does it only look it?

Reads one device through the Herder API and compares three things: what
the device exposes (its walked data model), what Herder reads from it
(its stored parameters), and what Herder shows (canonical coverage, the
service modules, the device page, the network map, the actions). Every
difference is a finding. The gate exits 1 while any finding is a FAIL.

    HERDER_API=https://acs.example.net HERDER_TOKEN=... \\
        tools/parity.py <serial-or-device-id> [--run-actions] [--waivers FILE]

A finding leaves the list one of two ways: the config repository wires
it, or a waiver states why it stays, with the evidence. There is no
third way and no scoping question. See .claude/skills/parity-gate.

Standard library, plus PyYAML to read the telemetry profiles (without
it, "read" falls back to "a value is stored", which a one-off read
satisfies). HERDER_CONNECT_ADDRESS connects to an address
instead of resolving the hostname (TLS is still verified against the
hostname), for a network where the name does not resolve.
"""

import argparse
import fnmatch
import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.parse

try:
    import yaml
except ImportError:
    yaml = None

PASS, FAIL, WAIVED, INFO = "PASS", "FAIL", "WAIVED", "INFO"

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- API


class API:
    def __init__(self, base, token, connect_address=""):
        u = urllib.parse.urlsplit(base.rstrip("/"))
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.prefix = u.path.rstrip("/")
        if not self.prefix.endswith("/api/v1"):
            self.prefix += "/api/v1"
        self.token = token
        self.connect_address = connect_address

    def _connection(self):
        port = self.port or (443 if self.scheme == "https" else 80)
        if self.scheme != "https":
            return http.client.HTTPConnection(self.connect_address or self.host, port, timeout=60)
        conn = http.client.HTTPSConnection(self.host, port, timeout=60, context=ssl.create_default_context())
        if self.connect_address:
            # Dial the address, keep the hostname for SNI and the certificate.
            conn.sock = conn._context.wrap_socket(
                socket.create_connection((self.connect_address, port), 60),
                server_hostname=self.host,
            )
        return conn

    def call(self, method, path, body=None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        # A release rolling under the gate answers 502 for a few seconds.
        for attempt in range(6):
            conn = self._connection()
            try:
                conn.request(method, self.prefix + path, body=data, headers=headers)
                response = conn.getresponse()
                raw = response.read()
            except OSError:
                response, raw = None, b""
            finally:
                conn.close()
            if response is not None and response.status not in (429, 502, 503, 504):
                break
            time.sleep(5)
        if response is None:
            sys.exit(f"{method} {path}: the API did not answer")
        try:
            return response.status, json.loads(raw) if raw else None
        except ValueError:
            return response.status, None

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body or {})

    def pages(self, path, limit=1000):
        """Every row of an offset-paged list."""
        rows, offset = [], 0
        sep = "&" if "?" in path else "?"
        while True:
            status, page = self.get(f"{path}{sep}limit={limit}&offset={offset}")
            data = (page or {}).get("data") or []
            rows += data
            if status != 200 or len(data) < limit:
                return rows
            offset += limit


# ------------------------------------------------------------ helpers


def normalise(path):
    """Device.Hosts.Host.12.Active -> Device.Hosts.Host.{i}.Active"""
    return re.sub(r"\.\d+(?=\.|$)", ".{i}", path)


def top_object(norm_path):
    """The object a leaf belongs to, two levels under the root:
    Device.WiFi.Radio.{i}.Channel -> Device.WiFi"""
    parts = norm_path.split(".")
    return ".".join(parts[:2])


def vendor_prefixes(norm_paths):
    out = set()
    for p in norm_paths:
        for seg in p.split("."):
            if seg.startswith("X_"):
                out.add("_".join(seg.split("_")[:2]))
    return out


class Report:
    def __init__(self, waivers):
        self.rows = []
        self.waivers = waivers
        self.used = set()

    def add(self, status, check, ident, detail):
        if status == FAIL:
            for i, w in enumerate(self.waivers):
                if fnmatch.fnmatch(f"{check}:{ident}", w["match"]):
                    self.used.add(i)
                    status, detail = WAIVED, f"{detail} | waived: {w['reason']} ({w['evidence']})"
                    break
        self.rows.append((status, check, ident, detail))

    def count(self, status):
        return sum(1 for r in self.rows if r[0] == status)


# ------------------------------------------------- what is collected


def device_labels(api, device):
    """The labels a selector sees, per the Device Selectors guide."""
    labels = {}
    for key, field in (
        ("oui", "oui"),
        ("manufacturer", "manufacturer"),
        ("productClass", "product_class"),
        ("model", "model"),
        ("firmwareVersion", "firmware_version"),
    ):
        if device.get(field):
            labels[key] = str(device[field])
    for dm in device.get("data_models") or []:
        labels[f"dataModel:{dm}"] = ""
    for proto in device.get("protocols") or []:
        labels[f"protocol:{proto}"] = ""
    for tag in device.get("tags") or []:
        labels[f"tag:{tag}"] = ""
    for key, value in (device.get("metadata") or {}).items():
        if isinstance(value, (str, int, float, bool)):
            labels[f"meta:{key}"] = str(value).lower() if isinstance(value, bool) else str(value)
    status, groups = api.get(f"/devices/{device['id']}/groups")
    for g in (groups or {}).get("data", []) if isinstance(groups, dict) else groups or []:
        if g.get("path"):
            labels[f"group_path:{g['path']}"] = ""
    return labels


def selector_matches(selector, labels):
    """None when the selector uses an operator this cannot evaluate."""
    selector = selector or {}
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != str(value):
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr.get("key"), expr.get("operator"), [str(v) for v in expr.get("values") or []]
        if op == "In":
            if labels.get(key) not in values:
                return False
        elif op == "NotIn":
            if labels.get(key) in values:
                return False
        elif op == "Exists":
            if key not in labels:
                return False
        elif op == "DoesNotExist":
            if key in labels:
                return False
        else:
            return None
    return True


def collected_matcher(api, device):
    """A predicate over normalised leaves: does a telemetry profile that
    selects this device ask for it? Returns (predicate, profile names),
    or (None, []) when the profiles cannot be read."""
    if yaml is None:
        return None, []
    status, entries = api.get("/config/entries?kind=TelemetryProfile&limit=500")
    entries = entries.get("data", []) if isinstance(entries, dict) else entries or []
    labels = device_labels(api, device)
    patterns, names = [], []
    for entry in entries:
        status, full = api.get(f"/config/entries/{entry['group']}/{entry['kind']}/{entry['name']}")
        try:
            spec = (yaml.safe_load((full or {}).get("content") or "") or {}).get("spec") or {}
        except yaml.YAMLError:
            continue
        if selector_matches(spec.get("deviceSelector"), labels) is not True:
            continue
        names.append(entry["name"])
        for param in spec.get("parameters") or []:
            path = param.get("path") if isinstance(param, dict) else param
            if not path:
                continue
            segments = [
                r"\{i\}" if seg == "*" or seg.isdigit() else re.escape(seg)
                for seg in path.rstrip(".").split(".")
            ]
            body = r"\.".join(segments) + (r"\." if path.endswith(".") else "")
            patterns.append(re.compile("^" + body + ("" if path.endswith(".") else "$")))
    return (lambda leaf: any(p.match(leaf) for p in patterns)), names


# ------------------------------------------------------------- checks


def find_device(api, ref):
    if re.fullmatch(r"[0-9a-f-]{36}", ref):
        status, device = api.get(f"/devices/{ref}")
        if status == 200:
            return device
    status, page = api.get(f"/devices?serial={urllib.parse.quote(ref)}&count=false&limit=50")
    exact = [d for d in (page or {}).get("data", []) if d["serial_number"].lower() == ref.lower()]
    if len(exact) != 1:
        sys.exit(f"{ref}: {len(exact)} devices match")
    return api.get(f"/devices/{exact[0]['id']}")[1]


def load_model(api, device):
    """The walked model for the device's tuple, or None. Returns
    (model row, {normalised path: writable})."""
    status, page = api.get("/schema/models?limit=500")
    rows = [
        m
        for m in (page or {}).get("data", [])
        if m["oui"] == device["oui"] and m["product_class"] == device["product_class"]
    ]
    if not rows:
        return None, {}
    current = [m for m in rows if m.get("firmware_version") == device.get("firmware_version")]
    model = sorted(current or rows, key=lambda m: m["discovered_at"])[-1]
    leaves, cursor = {}, ""
    while True:
        q = f"/schema/models/{model['id']}/parameters?limit=1000"
        if cursor:
            q += "&cursor=" + urllib.parse.quote(cursor)
        status, page = api.get(q)
        data = (page or {}).get("data") or []
        for row in data:
            if not row["path"].endswith("."):
                leaves[normalise(row["path"])] = row.get("writable")
        if len(data) < 1000:
            return model, leaves
        cursor = data[-1]["path"]


def check_model(report, device, model, leaves):
    if model is None:
        report.add(
            FAIL,
            "model",
            "walked",
            "no discovered data model for this tuple, so nothing below can call a path absent. "
            "Walk it: POST /api/v1/schema/discover, or run this with --discover",
        )
        return
    detail = f"{model['parameter_count']} parameters, {len(leaves)} distinct leaves, walked {model['discovered_at'][:10]} at firmware {model.get('firmware_version')}"
    if model.get("truncated"):
        report.add(FAIL, "model", "truncated", detail + "; the walk was truncated, walk it level by level")
    elif model.get("firmware_version") != device.get("firmware_version"):
        report.add(
            FAIL,
            "model",
            "firmware",
            detail + f"; the device now runs {device.get('firmware_version')}, walk it again",
        )
    else:
        report.add(PASS, "model", "walked", detail)


def absent_or_unread(leaves, model, raw_path, collected=None):
    """Classify a path Herder maps and holds no value for."""
    if model is None or not raw_path:
        return FAIL, "no value stored, and no walked model to say whether the device has it"
    leaf = normalise(raw_path)
    if leaf in leaves:
        if collected and collected(leaf):
            return FAIL, f"a telemetry profile asks for {raw_path} and no value is stored: the device faults it or has not been read since"
        return FAIL, f"the device has {raw_path} and no telemetry profile reads it"
    return INFO, f"{raw_path} is not in the walked model, so this hardware does not carry it"


def check_coverage(report, api, device, model, leaves, collected):
    status, cov = api.get(f"/devices/{device['id']}/canonical-coverage")
    if status != 200:
        report.add(FAIL, "coverage", "read", f"canonical-coverage answered {status}")
        return None
    for feature in cov.get("features", []):
        for c in feature["canonicals"]:
            name = c["name"]
            if c["status"] == "bound":
                continue
            if c["status"] == "unmapped":
                report.add(FAIL, "coverage", name, f"[{feature['name']}] no mapping table binds it for this profile")
            else:
                s, why = absent_or_unread(leaves, model, c.get("raw_path"), collected)
                if c.get("owner"):
                    s, why = INFO, f"held by {c['owner']}, not read from the device"
                report.add(s, "coverage", name, f"[{feature['name']}] {why}")
    return cov.get("profile")


def check_modules(report, api, device, model, leaves, collected):
    status, catalogue = api.get("/modules")
    names = [m["name"] for m in (catalogue or {}).get("modules", [])]
    for name in names:
        status, mod = api.get(f"/devices/{device['id']}/modules/{name}")
        if status != 200:
            report.add(FAIL, "module", name, f"answered {status}")
            continue
        resolved = len(mod.get("fields") or {}) + sum(len(v) for v in (mod.get("collections") or {}).values())
        unresolved = mod.get("unresolved") or []
        if not unresolved:
            report.add(PASS if resolved else INFO, "module", name, f"{resolved} resolved, nothing unresolved")
        for u in unresolved:
            ident = f"{name}.{u.get('field')}"
            reason = u.get("reason")
            if reason == "raw_missing":
                s, why = absent_or_unread(leaves, model, u.get("raw_path"), collected)
                report.add(s, "module", ident, why)
            elif reason == "canonical_unmapped":
                report.add(FAIL, "module", ident, f"{u.get('canonical')} has no mapping for this profile")
            else:
                report.add(FAIL, "module", ident, f"unresolved: {reason}")


def check_page(report, api, device):
    """Every canonical the device page renders must resolve."""
    status, profile = api.get(f"/devices/{device['id']}/ui-profile")
    if status != 200:
        report.add(FAIL, "page", "profile", f"ui-profile answered {status}")
        return
    wanted = {}

    def walk(node, section):
        if isinstance(node, dict):
            if isinstance(node.get("canonical"), str):
                wanted.setdefault(node["canonical"], section)
            for v in node.values():
                walk(v, section)
        elif isinstance(node, list):
            for v in node:
                walk(v, section)

    for section in profile.get("sections", []):
        walk(section.get("config"), section.get("title") or section.get("key"))
    names = sorted(wanted)
    missing = {}
    for i in range(0, len(names), 100):
        status, res = api.post(f"/devices/{device['id']}/canonical-resolve", {"patterns": names[i : i + 100]})
        for u in (res or {}).get("unresolved", []):
            missing[u["canonical"]] = u.get("reason")
    for name, reason in sorted(missing.items()):
        report.add(FAIL, "page", name, f"the {wanted.get(name, '?')} section renders it and it does not resolve ({reason})")
    report.add(
        PASS if not missing else INFO,
        "page",
        profile.get("profile_name", "?"),
        f"{len(names) - len(missing)} of {len(names)} rendered canonicals resolve across {len(profile.get('sections', []))} sections",
    )


def load_features():
    with open(os.path.join(HERE, "features.json")) as f:
        return json.load(f)


def check_objects(report, leaves, collected_leaves, stored_leaves, model):
    """Every object the device exposes is read, has no platform surface,
    or is a finding. This is the check that an onboarding which only
    wired what somebody thought to ask for cannot pass."""
    if model is None:
        return
    features = load_features()
    by_object = {}
    for leaf in leaves:
        by_object.setdefault(top_object(leaf), []).append(leaf)

    def surface_for(leaf):
        for f in features["features"]:
            if any(re.search(p, leaf) for p in f["paths"]):
                return f
        return None

    groups = {}
    unknown = {}
    for leaf in leaves:
        f = surface_for(leaf)
        if f is None:
            unknown.setdefault(top_object(leaf), []).append(leaf)
        else:
            groups.setdefault(f["name"], (f, []))[1].append(leaf)

    for name, (f, members) in sorted(groups.items()):
        # An object the CPE volunteers on every Inform is read without a
        # profile asking for it, so there a stored value is the evidence.
        stored_norm = collected_leaves | stored_leaves if f.get("informed") else collected_leaves
        read = [m for m in members if m in stored_norm]
        detail = f"{len(read)} of {len(members)} leaves read. {f['surface']}"
        if not f.get("platform", True):
            report.add(INFO, "object", name, detail)
        elif not read:
            report.add(FAIL, "object", name, f"the device exposes it and nothing reads it. {f['surface']}")
        else:
            wanted = [m for m in members if any(re.search(p, m) for p in f.get("expect", []))]
            lost = [m for m in wanted if m not in stored_norm]
            if lost:
                report.add(FAIL, "object", name, detail + " Not read: " + ", ".join(lost[:8]) + (" ..." if len(lost) > 8 else ""))
            else:
                report.add(PASS, "object", name, detail)

    for obj, members in sorted(unknown.items()):
        read = [m for m in members if m in collected_leaves]
        vendor = sorted(vendor_prefixes(members))
        label = f"{obj}" + (f" ({', '.join(vendor)})" if vendor else "")
        if read:
            report.add(INFO, "object", label, f"{len(read)} of {len(members)} leaves read; no entry in tools/features.json")
        else:
            report.add(
                FAIL,
                "object",
                label,
                f"{len(members)} leaves on the device, none read, and tools/features.json does not know the object. "
                "Decide: wire it, or waive it with what it is",
            )


def check_topology(report, api, device, stored):
    status, topo = api.get(f"/devices/{device['id']}/topology")
    nodes = (topo or {}).get("nodes") or []
    edges = (topo or {}).get("edges") or []
    if status != 200 or not nodes:
        report.add(FAIL, "map", "present", "no network map: no topology rule ran for this device, or it wrote nothing")
        return
    kinds = {}
    for n in nodes:
        kinds.setdefault(n["type"], []).append(n)
    if "gateway" not in kinds and "extender" not in kinds:
        report.add(FAIL, "map", "root", "the map has no gateway node")
    clients = kinds.get("client", [])
    uplink = {e["child"]: e for e in edges}

    hosts = {}
    for path, value in stored.items():
        m = re.match(r"(.*\.Hosts\.Host\.\d+)\.(PhysAddress|MACAddress|Active|HostName|IPAddress)$", path)
        if m:
            hosts.setdefault(m.group(1), {})[m.group(2)] = value
    active = {
        (h.get("PhysAddress") or h.get("MACAddress") or "").lower(): h
        for h in hosts.values()
        if str(h.get("Active", "")).lower() in ("true", "1") and (h.get("PhysAddress") or h.get("MACAddress"))
    }
    on_map = {n["id"].lower() for n in nodes}
    lost = sorted(mac for mac in active if mac not in on_map)
    if active:
        report.add(
            FAIL if len(lost) > len(active) * 0.2 else PASS,
            "map",
            "hosts",
            f"{len(active) - len(lost)} of {len(active)} active hosts are on the map" + (f"; missing {', '.join(lost[:5])}" if lost else ""),
        )
    named = {mac for mac, h in active.items() if h.get("HostName")}
    unnamed = [n for n in clients if n["id"].lower() in named and not (n.get("properties") or {}).get("hostname")]
    if named:
        report.add(
            FAIL if unnamed else PASS,
            "map",
            "hostnames",
            f"{len(unnamed)} clients the host table names have no hostname on the map" if unnamed else "every named host carries its hostname",
        )
    addressed = {mac for mac, h in active.items() if h.get("IPAddress")}
    bare = [n for n in clients if n["id"].lower() in addressed and not (n.get("properties") or {}).get("ipv4")]
    if addressed:
        report.add(
            FAIL if bare else PASS,
            "map",
            "addresses",
            f"{len(bare)} clients the host table addresses have no ipv4 on the map" if bare else "every addressed host carries its address",
        )
    wifi = [n for n in clients if (uplink.get(n["id"]) or {}).get("edge_type", "").startswith("wifi")]
    silent = [
        n
        for n in wifi
        if "rssi_dbm" not in ((uplink[n["id"]].get("link_metrics")) or {}) and "signal_dbm" not in (n.get("properties") or {})
    ]
    if wifi:
        report.add(
            FAIL if silent else PASS,
            "map",
            "signal",
            f"{len(silent)} of {len(wifi)} WiFi clients have no signal" if silent else f"all {len(wifi)} WiFi clients carry a signal",
        )
    for ext in kinds.get("extender", []):
        edge = uplink.get(ext["id"]) or {}
        props = ext.get("properties") or {}
        gaps = [k for k in ("serial", "model", "firmware") if not props.get(k)]
        if edge.get("edge_type", "").startswith("wifi") and "rssi_dbm" not in (edge.get("link_metrics") or {}):
            gaps.append("backhaul signal")
        if not props.get("ipv4"):
            gaps.append("ipv4")
        report.add(FAIL if gaps else PASS, "map", f"extender {ext['id']}", "missing " + ", ".join(gaps) if gaps else "identity, address and backhaul present")
    ports = [n for n in kinds.get("interface", [])]
    lan = {
        re.match(r"(.*\.Ethernet\.Interface\.\d+)\.", p).group(1)
        for p, v in stored.items()
        if re.match(r".*\.Ethernet\.Interface\.\d+\.Upstream$", p) and str(v).lower() in ("false", "0")
    }
    if lan:
        report.add(
            FAIL if len(ports) < len(lan) else PASS,
            "map",
            "ports",
            f"{len(ports)} port nodes for {len(lan)} LAN ports" + ("; ports that share a MAC collapse into one node" if len(ports) < len(lan) else ""),
        )
    odd = [n for n in ports if "/" in ((n.get("properties") or {}).get("name") or "")]
    if odd:
        report.add(FAIL, "map", "port names", f"{len(odd)} ports are named by an internal path, such as {(odd[0]['properties'])['name']}")


SAFE_ACTIONS = {
    "ping": {"host": "one.one.one.one"},
    "dns_lookup": {"hostname": "one.one.one.one"},
    "traceroute": {"host": "one.one.one.one"},
    "wifi_scan": {},
    "self_test": {},
}


def check_actions(report, api, device, run):
    status, listing = api.get(f"/devices/{device['id']}/actions")
    caps = (listing or {}).get("capabilities") or []
    if not caps:
        report.add(FAIL, "action", "offered", "no capability is offered to this device")
        return
    for cap in caps:
        name = cap["capability"]
        if not run:
            report.add(INFO, "action", name, f"offered through {cap['profile']}; not exercised (pass --run-actions)")
            continue
        if name not in SAFE_ACTIONS:
            report.add(INFO, "action", name, f"offered through {cap['profile']}; not run by the gate, it loads the line or changes the device")
            continue
        status, started = api.post(f"/devices/{device['id']}/actions/{name}", {"inputs": SAFE_ACTIONS[name]})
        if status != 202:
            report.add(FAIL, "action", name, f"start answered {status}: {json.dumps(started)[:200]}")
            continue
        deadline, result = time.time() + 240, {}
        while time.time() < deadline:
            time.sleep(4)
            status, result = api.get(f"/action-runs/{started['run_id']}")
            if (result or {}).get("status") not in ("pending", "running"):
                break
        state = (result or {}).get("status")
        payload = (result or {}).get("result") or {}
        empty = [k for k, v in payload.items() if v is None]
        if state != "completed":
            report.add(FAIL, "action", name, f"run ended {state}: {(result or {}).get('error_message')}")
        elif not payload:
            report.add(FAIL, "action", name, "completed with no result")
        else:
            report.add(PASS, "action", name, f"completed through {cap['profile']}" + (f"; null in result: {', '.join(empty)}" if empty else ""))


def check_signals(report, api, device, stored):
    has_clients = any(re.search(r"AssociatedDevice\.\d+\.", p) for p in stored)
    status, rows = api.get(f"/devices/{device['id']}/labeled-telemetry?metric=wifi.client.rssi&limit=1")
    if has_clients:
        ok = bool((rows or {}).get("data"))
        report.add(PASS if ok else FAIL, "wifi", "client signal", "per-client signal rows exist" if ok else "the device reports associated clients and no wifi.client.rssi row exists: no labels rule matches")
    if device.get("score") is None:
        report.add(FAIL, "experience", "score", "the device has no experience score")
    else:
        report.add(PASS, "experience", "score", f"{device['score']:.0f}")
    status, faults = api.get(f"/devices/{device['id']}/faults?limit=200")
    day_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 86400))
    recent = {}
    for f in (faults or {}).get("data", []):
        if (f.get("observed_at") or "") >= day_ago:
            recent.setdefault(f.get("code"), []).append(f)
    for code, rows in sorted(recent.items()):
        rule = (rows[0].get("context") or {}).get("rule_name")
        report.add(FAIL, "faults", str(code), f"{len(rows)} in the last day" + (f" from {rule}" if rule else "") + f": {rows[0].get('message', '')[:200]}")
    status, rejected = api.get(f"/devices/{device['id']}/parameters/rejected?limit=200")
    paths = [r["path"] for r in (rejected or {}).get("data", [])]
    if paths:
        report.add(FAIL, "telemetry", "rejected", f"the device faults {len(paths)} paths a profile asks for: {', '.join(paths[:6])}")


def discover(api, device):
    status, body = api.post("/schema/discover", {"device_id": device["id"]})
    print(f"discover: {status} {json.dumps(body)[:200]}")
    api.post(f"/devices/{device['id']}/connection-request")


# --------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="The parity gate for one device.")
    ap.add_argument("device", help="serial number or device id")
    ap.add_argument("--waivers", help="JSON waivers file from the config repository")
    ap.add_argument("--run-actions", action="store_true", help="run the capabilities that are safe to run")
    ap.add_argument("--discover", action="store_true", help="queue a data model walk and exit")
    ap.add_argument("--json", action="store_true", help="print the findings as JSON")
    ap.add_argument("--all", action="store_true", help="print PASS rows too")
    args = ap.parse_args()

    base, token = os.environ.get("HERDER_API"), os.environ.get("HERDER_TOKEN")
    if not base or not token:
        sys.exit("HERDER_API and HERDER_TOKEN must be set")
    api = API(base, token, os.environ.get("HERDER_CONNECT_ADDRESS", ""))

    waivers = []
    if args.waivers:
        with open(args.waivers) as f:
            waivers = json.load(f)["waivers"]
        for w in waivers:
            for key in ("match", "reason", "evidence"):
                if not w.get(key):
                    sys.exit(f"waiver {w} needs match, reason and evidence")

    device = find_device(api, args.device)
    if args.discover:
        discover(api, device)
        return

    report = Report(waivers)
    stored = {r["path"]: r["value"] for r in api.pages(f"/devices/{device['id']}/parameters", 5000)}
    model, leaves = load_model(api, device)
    collected, profiles = collected_matcher(api, device)
    if collected is None:
        read = {normalise(p) for p in stored}
        how = "judged by stored values, PyYAML is not installed"
    else:
        read = {leaf for leaf in leaves if collected(leaf)}
        how = "by " + (", ".join(profiles) or "no telemetry profile")

    check_model(report, device, model, leaves)
    profile = check_coverage(report, api, device, model, leaves, collected)
    check_modules(report, api, device, model, leaves, collected)
    check_page(report, api, device)
    check_objects(report, leaves, read, {normalise(p) for p in stored}, model)
    check_topology(report, api, device, stored)
    check_signals(report, api, device, stored)
    check_actions(report, api, device, args.run_actions)
    for i, w in enumerate(waivers):
        if i not in report.used:
            report.add(FAIL, "waiver", w["match"], "matches no finding: the gap is gone or the pattern is wrong, remove it")

    if args.json:
        print(json.dumps([dict(zip(("status", "check", "id", "detail"), r)) for r in report.rows], indent=2))
    else:
        print(
            f"{device.get('manufacturer')} {device.get('product_class')} {device['serial_number']} "
            f"firmware {device.get('firmware_version')} profile {profile}"
        )
        print(f"{len(stored)} parameters stored, {len(read & set(leaves))} of {len(leaves)} model leaves read {how}\n")
        order = {FAIL: 0, WAIVED: 1, INFO: 2, PASS: 3}
        for status, check, ident, detail in sorted(report.rows, key=lambda r: (order[r[0]], r[1], r[2])):
            if status == PASS and not args.all:
                continue
            print(f"{status:6} {check}: {ident}\n         {detail}")
        print(
            f"\n{report.count(FAIL)} FAIL, {report.count(WAIVED)} waived, "
            f"{report.count(INFO)} info, {report.count(PASS)} pass"
        )
    sys.exit(1 if report.count(FAIL) else 0)


if __name__ == "__main__":
    main()
