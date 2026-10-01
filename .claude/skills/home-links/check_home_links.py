#!/usr/bin/env python3
"""Check that every home on a Herder instance is linked the same from both ends.

Reads every device's topology and reports each place the gateway and
extender links disagree. Standard library only.

    export HERDER_API=https://acs.example.net HERDER_TOKEN=...
    python3 check_home_links.py                  # report
    python3 check_home_links.py --save before.json
    python3 check_home_links.py --compare before.json

Exit status is 0 when nothing is wrong, 1 when a check failed, 2 when
the instance could not be read.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Node types that are a part of a unit, never a unit.
PARTS = {"ssid", "interface"}


def get(api, token, path):
    request = urllib.request.Request(
        api.rstrip("/") + path, headers={"Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def all_devices(api, token):
    devices, offset = [], 0
    while True:
        page = get(api, token, f"/api/v1/devices?limit=200&offset={offset}")
        devices += page["data"]
        offset += len(page["data"])
        if not page.get("next") or not page["data"]:
            return devices


def check(devices, topologies):
    """Every failed check as (kind, serial, detail)."""
    serial = {d["id"]: d["serial_number"] for d in devices}
    name = lambda i: serial.get(i, i)  # noqa: E731
    failures, notes = [], []

    for device in devices:
        did = device["id"]
        topo = topologies.get(did)
        if topo is None or "nodes" not in topo:
            failures.append(("unreadable", name(did), "topology read failed"))
            continue
        nodes = {n["id"]: n for n in topo["nodes"]}
        children = {e["child"] for e in topo["edges"] if e["parent"] != e["child"]}
        upstream = topo.get("upstream")

        for node in topo["nodes"]:
            managed = node.get("managed_device_id")
            if managed and managed != did and node["type"] in PARTS:
                failures.append(
                    ("part linked as a unit", name(did),
                     f"{node['type']} node {node['id']} is linked to {name(managed)}")
                )
            if node["type"] == "extender" and not managed and not upstream:
                props = node.get("properties") or {}
                notes.append(
                    ("extender not managed", name(did),
                     f"serial {props.get('serial', 'unknown')}")
                )

        if nodes:
            roots = [n for n in topo["nodes"] if n["id"] not in children]
            gateways = [n for n in topo["nodes"] if n["type"] == "gateway"]
            if len(gateways) > 1:
                failures.append(("two gateways on one map", name(did),
                                 ", ".join(n["id"] for n in gateways)))
            stray = [n for n in roots if n["type"] != "gateway"]
            if gateways and stray:
                failures.append(("nodes attached to nothing", name(did),
                                 ", ".join(f"{n['type']} {n['id']}" for n in stray[:5])))

        extenders = [
            n["managed_device_id"] for n in topo["nodes"]
            if n["type"] == "extender" and n.get("managed_device_id") not in (None, did)
        ]

        if upstream:
            gateway = upstream["device_id"]
            theirs = topologies.get(gateway) or {}
            if theirs.get("upstream"):
                back = theirs["upstream"]["device_id"]
                if back == did:
                    failures.append(("each names the other as its gateway",
                                     name(did), name(gateway)))
            if upstream.get("node_id") is None:
                notes.append(("linked by name only", name(did),
                              f"{name(gateway)} has no node for it"))
            elif nodes and "nodes" in theirs:
                mine, home = set(nodes), {n["id"] for n in theirs["nodes"]}
                if mine != home:
                    failures.append(
                        ("extender and gateway show different homes", name(did),
                         f"{len(mine - home)} nodes only here, "
                         f"{len(home - mine)} only on {name(gateway)}")
                    )
        else:
            for extender in extenders:
                theirs = topologies.get(extender) or {}
                back = (theirs.get("upstream") or {}).get("device_id")
                if back != did:
                    failures.append(
                        ("gateway links an extender that does not link back",
                         name(did),
                         f"{name(extender)} names {name(back) if back else 'no gateway'}")
                    )
    return failures, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", metavar="FILE",
                        help="write which devices have a gateway, for --compare later")
    parser.add_argument("--compare", metavar="FILE",
                        help="fail when a device that had a gateway in FILE has none now")
    args = parser.parse_args()

    api, token = os.environ.get("HERDER_API"), os.environ.get("HERDER_TOKEN")
    if not api or not token:
        print("HERDER_API and HERDER_TOKEN must be set", file=sys.stderr)
        return 2
    try:
        devices = all_devices(api, token)

        def read(device):
            try:
                return device["id"], get(api, token, f"/api/v1/devices/{device['id']}/topology")
            except (urllib.error.URLError, ValueError):
                return device["id"], None

        with ThreadPoolExecutor(max_workers=8) as pool:
            topologies = dict(pool.map(read, devices))
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        print(f"cannot read the instance: {exc}", file=sys.stderr)
        return 2

    failures, notes = check(devices, topologies)
    serial = {d["id"]: d["serial_number"] for d in devices}
    linked = {
        serial[i]: serial.get(t["upstream"]["device_id"], t["upstream"]["device_id"])
        for i, t in topologies.items() if t and t.get("upstream")
    }

    if args.compare:
        with open(args.compare) as handle:
            before = json.load(handle)
        for unit, gateway in sorted(before.items()):
            if unit not in linked:
                failures.append(("lost its gateway", unit, f"was behind {gateway}"))
            elif linked[unit] != gateway:
                failures.append(("changed gateway", unit, f"{gateway} to {linked[unit]}"))
    if args.save:
        with open(args.save, "w") as handle:
            json.dump(linked, handle, indent=2, sort_keys=True)

    print(f"{len(devices)} devices, {len(linked)} behind a managed gateway")
    for kind, unit, detail in notes:
        print(f"note  {kind}: {unit} ({detail})")
    for kind, unit, detail in failures:
        print(f"FAIL  {kind}: {unit} ({detail})")
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
