---
name: home-links
description: Link a vendor's extenders to their gateways so every unit in a home shows the same network map, and prove it across the fleet. Use when extenders are managed devices of their own, when an extender's page shows no gateway or an empty map, when a gateway's map shows an extender as a plain client, or before and after any change to topology rules or a Herder upgrade.
---

# Extenders and their gateways

Goal: every managed extender names its gateway, every gateway's map
carries its extenders with their SSIDs and clients under them, and the
map is the same picture from every unit in the home. Requires a
surveyed model (`survey-datamodel`) for the gateway and for the
extender. They are often the same product class and still two
different trees.

## 1. How Herder links them

A topology read is of the home, not of one unit. Two facts tie an
extender to its gateway, and a vendor integration has to supply at
least one:

- **The gateway the extender names.** A LAN-side device learns its
  gateway's identity over DHCP and reports it under
  `Device.GatewayInfo` (OUI, product class, serial). The
  `topology-gateway` rule emits it as a `topology.gateway` row and
  Herder resolves the OUI and serial to a managed device. This is
  checked first.
- **An address both sides report.** The `topology-identity` rule
  emits every address a device reports as its own (Ethernet MACs,
  BSSIDs, SSID MACs). A node in the gateway's graph at one of those
  addresses is that extender.

The read then joins the graphs: on the gateway, each managed
extender's own SSIDs and clients hang under its node; on the extender,
the response is that same home graph with an `upstream` naming the
gateway. The Topology Data Model guide has the full rules.

## 2. Save the fleet's links before touching anything

```bash
python3 .claude/skills/home-links/check_home_links.py --save before.json
```

It reads every device's topology and records which unit is behind
which gateway. Run it before a rule change, a bundle move or a Herder
upgrade, and again with `--compare before.json` after. A unit that had
a gateway and has none afterwards is a regression, whatever the change
was meant to do, and nothing else will tell you: a lost link renders
as an ordinary device with an empty map.

## 3. Read what the extender says about its gateway

One leaf per request. A CPE that rejects one name rejects the whole
request, so three leaves read together and refused prove nothing about
any of them.

```bash
curl -s -X POST "$HERDER_API/api/v1/tasks" -H "Authorization: Bearer $HERDER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"<id>","task_type":"GetParameterValues",
       "payload":{"parameter_names":["Device.GatewayInfo.SerialNumber"]},
       "idempotency_key":"<uuid>"}'
```

Read the task's `result` once it completes, and read all three leaves.
What has been seen on real hardware:

- **Filled and correct.** The serial and OUI are the ones the gateway
  informs with. Nothing to write: the baseline collects the object and
  the bundled rule emits the row.
- **Filled with the unit's own identity.** Some firmware reports
  itself as its gateway. Herder treats a self-claim as no claim and
  falls back to the address match, so this needs the address match to
  work (step 4).
- **Empty.** The address match is the only link.
- **Never collected.** A previous ACS that did not read the object
  left no values behind, and "mostly empty" in its export means it was
  not asked. Read it from the device.
- **Under a vendor tree.** A mesh controller's serial under a vendor
  prefix goes in a vendor rule that emits the same `topology.gateway`
  row with `oui` and `serial`.

An extender sits behind its gateway's NAT. An HTTP connection request
to its LAN address cannot reach it, so a task waits for its next
session unless it holds an XMPP session. A task at `dispatched` has
not been delivered: compare the device's `last_contact` with the
task's `created_at` before concluding the device is ignoring it.

## 4. Find the address the gateway knows the extender by

Take the node ids in the gateway's graph and the extender's own
addresses, and look for the one they share:

```bash
curl -s "$HERDER_API/api/v1/devices/<gateway_id>/topology" \
  -H "Authorization: Bearer $HERDER_TOKEN" | jq -r '.nodes[] | "\(.type) \(.id)"'
curl -s "$HERDER_API/api/v1/devices/<extender_id>/parameters?search=MACAddress&limit=500" \
  -H "Authorization: Bearer $HERDER_TOKEN" | jq -r '.data[] | "\(.path) \(.value)"'
curl -s "$HERDER_API/api/v1/devices/<extender_id>/parameters?search=BSSID&limit=500" \
  -H "Authorization: Bearer $HERDER_TOKEN" | jq -r '.data[] | "\(.path) \(.value)"'
```

A wired extender is usually in the gateway's host table under an
Ethernet MAC. A wireless one associates from a radio, so the gateway
lists it under a BSSID or an SSID's MAC address, and nothing
`Device.Ethernet` reports will match. If the shared address is under a
path the identity rule does not publish, add a vendor rule that emits
`topology.identity` for it. Only addresses the unit reports as its
own: never a host table, an association table or a scan.

Leave out of `own` addresses any that a fleet shares. A factory image
with one MAC on every unit links nothing, by design.

## 5. Expect to be seen from both ends

A wireless backhaul is two radios, and each unit lists the other:

- The extender's graph carries its gateway's radio as a station on
  its backhaul SSID.
- The extender's backhaul SSID node sits at the same address the
  gateway knows the extender by.

Herder folds both into the one link the gateway draws. What a vendor
rule must not do is make it worse: do not emit the far end of the
backhaul as a `client`, and do not bind `managed_device_id` on a node
that is another unit. Set it only on the node that is the device the
script is running for.

## 6. Check the map against a fresh read, not against itself

A map is a snapshot. A rule with a `changeHash` re-emits only when a
structural field changes, so a snapshot can be a day old, and a map
that looks complete can be missing everything that happened since.
Before trusting a client count, read the association table now:

```bash
# one task, the unit's access points and SSIDs
"parameter_names": ["Device.WiFi.AccessPoint.", "Device.WiFi.SSID."]
```

Then compare with the map:

- The stations in `AssociatedDevice` under each access point are the
  clients that should hang under that SSID.
- Every SSID that is `Up` and named should be a node. A network the
  firmware reports with an empty `BSSID` has its address in
  `MACAddress`; a script that takes the node id from the BSSID alone
  drops the network and leaves its clients hanging off the unit with
  an untyped link.
- A client drawn directly under a gateway or extender with edge type
  `other` is a client the script could not place. Find out why before
  calling the map done.

## 7. Prove it from both pages and across the fleet

Open the gateway's topology and the extender's. They must be the same
nodes. Then run the check over everything:

```bash
python3 .claude/skills/home-links/check_home_links.py --compare before.json
```

It fails on:

| Failure | What it means |
|---------|---------------|
| lost its gateway | a unit linked before the change is not linked now |
| each names the other as its gateway | the link was resolved in both directions |
| two gateways on one map | a unit behind the gateway was drawn as one |
| part linked as a unit | an SSID or interface node carries another device's id |
| gateway links an extender that does not link back | the two ends disagree |
| extender and gateway show different homes | the reads were joined differently |
| nodes attached to nothing | a node no edge reaches, beside a gateway |

Notes it prints are not failures: an extender the gateway reports that
is not a managed device, and a unit linked by name whose gateway's
graph has no node for it (step 4 is unfinished for that model).

Run it on every instance the change reaches, not only the one it was
written against. Two fleets with different vendors fail in different
places: a rule proven on one vendor's wireless extenders removed the
links of another vendor's, whose firmware named itself as its gateway.

## 8. An extender page that is empty where the gateway's is full

An extender is often a smaller tree than its gateway on the same
hardware, and some answer faults to subtree reads while every leaf
reads fine alone. Before reporting that an extender exposes no WiFi:

```bash
curl -s "$HERDER_API/api/v1/devices/<extender_id>/parameters/rejected?limit=5000" \
  -H "Authorization: Bearer $HERDER_TOKEN" | jq -r '.data[] | "\(.last_seen) \(.path)"'
```

That is every path the unit has refused and when. Leaves refused one
at a time are a real answer; a refused subtree is not, and neither is
a discovered model built from subtree walks. Compare two units of the
same model and firmware: when one has the data and another does not,
the firmware is not the reason. Check the dates on the one that has
it, since values collected before the unit became an extender still
render and look current.

Where the extender truly exposes nothing, its radios and clients are
in the gateway's tree (a mesh controller reports every agent through
`WiFi.DataElements`), and the extender's map and WiFi come from there.

## What to hand back

The check's output before and after, the address the gateway knows the
extender by and which rule publishes it, what `Device.GatewayInfo`
holds on the extender, and the station count from a fresh read beside
the count on the map. If any of the four is missing, the link is not
proven.
