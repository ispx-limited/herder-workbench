---
name: wifi-experience
description: Wire a new vendor's WiFi surface into Herder, per-client signal telemetry, mesh, and neighbour scans. Use after onboard-vendor's mapping work, or when asked why WiFi insights are empty for a vendor.
---

# WiFi experience for a new vendor

Goal: per-client signal rows flowing as labeled telemetry, and the
neighbour scan runnable as an action, for a vendor whose WiFi surface
lives behind vendor extensions. Requires a surveyed model
(`survey-datamodel`).

## 1. Find the vendor's client table

Standard trees put associated clients at
`Device.WiFi.AccessPoint.{i}.AssociatedDevice.{i}.` (TR-181) or
`InternetGatewayDevice.LANDevice.1.WLANConfiguration.{i}.AssociatedDevice.{i}.`
(TR-098), but the fields that matter (RSSI, rates, time associated)
are usually vendor extensions under the `X_<OUI>_` prefix. Search the
model:

```bash
curl -s "$HERDER_API/api/v1/schema/models/<model_id>/parameters?search=AssociatedDevice&limit=200" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Also identify the radio-to-band mapping: which `WLANConfiguration`
or `Radio` instances are 2.4 and 5 GHz, from a band or frequency
parameter. Instance numbers are not a convention; read them from the
device.

## 2. Stream it

A TelemetryProfile collecting the client MAC, the vendor RSSI, and
whatever rate fields exist, all `interval: 0` (passive, riding the
sessions that already happen). Then an EnrichmentRule with a labels
script turning raw rows into per-client labeled metrics; model both
on an existing vendor's pair in the config repo, and check every path
against the survey rather than copying any.

## 3. Verify honestly, and know this trap

Labeled rows appear only after a session carries the new paths, so
allow an inform cycle. Then:

```bash
curl -s "$HERDER_API/api/v1/devices/<device_id>/labeled-telemetry?metric=wifi.client.rssi&limit=5" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

**Empty rows with a correct rule usually means the device, not the
config.** Observed on real hardware: an idle associated client
reported RSSI 0, bytes 0, time associated 0, and the enrichment rule
rightly emitted nothing. Check the stored parameters first:

```bash
curl -s "$HERDER_API/api/v1/devices/<device_id>/parameters?limit=500" \
  -H "Authorization: Bearer $HERDER_TOKEN" | grep -i AssociatedDevice
```

If the raw values are zeros, the verification needs a live client
actually using the network near the AP; report that as the blocker
instead of rewriting a working rule. This is `survey-datamodel`'s
present but empty: the table is there and the device is idle. An empty
table on an idle unit is never evidence that a feature is missing.

## 4. The network map

The topology view is built by an EnrichmentRule that emits nodes and
edges (`topology.addNode`, `topology.addEdge`,
`topology.addEdgeMetric` in `types/sdk.d.ts`). `platform/topology/`
covers `WiFi.MultiAP` and `WiFi.DataElements` and the hosts table; a
vendor that keeps its mesh under its own table (ARRIS HNC, ASUS
AiMesh) needs its own rule, modelled on
`vendors/arris/hnc-topology.yaml` and `arris-hnc.ts`:

- The gateway node's id is a MAC read from the device, never made
  up: the mesh table's own entry for the router, or the LAN
  interface's MAC.
- One `extender` node per vendor node with a `wifi_backhaul` or
  `ethernet` edge to the gateway, and the backhaul signal as
  `rssi_dbm` on that edge.
- One `client` node per station in the vendor's per-node client
  table, edged to the node that serves it and typed by band; hostname
  and address joined from `Hosts.Host` by MAC.
- `triggerPaths` name paths that only a full GPV batch carries (the
  mesh table itself), so a small periodic Inform does not run the
  script against an event with no anchors.

Rules all run, highest priority first; a vendor rule at 100 beside the
platform ones is the shipped shape. Verify with
`GET /api/v1/devices/<device_id>/topology` after a session that
carried the mesh paths: the node count is the mesh table's, not one.

### Prove the map with a real client

A map that matches the mesh table can still be wrong for the one thing
a support agent uses it for: where a client is right now. Prove it on
the unit with a phone, while watching:

```bash
curl -sN -H "Authorization: Bearer $HERDER_TOKEN" \
  "$HERDER_API/api/v1/events/stream?topics=device.topology&device_id=<device_id>"
```

That stream is what the device page holds, and on a USP device it makes
the device live: the `baseline/usp/topology-live.yaml` block
(`whileWatched: true`) goes on the agent and it pushes the map every few
seconds. Join a phone to each SSID in turn, then take it off. Each
change should arrive as a `device.topology.updated` frame within
seconds; read `GET /api/v1/devices/<device_id>/topology` after each and
check:

- The client's edge has its SSID node (that SSID's BSSID) as parent and
  a band type (`wifi_2g`, `wifi_5g`, `wifi_6g`), never `other` and never
  hung straight off the gateway.
- It carries its hostname once the host table has it.
- It is gone from the map after it leaves, whatever the host table
  still says.
- No hidden, idle SSID is drawn as a node.

When it is wrong, find out why on the stored state rather than by
guessing: `POST /api/v1/topology/preview` runs a rule, with a modified
`script_body` if you give one, against the device's current parameters
and returns the graph it would draw. Fix it by the quirk rule in
CLAUDE.md. A TR-069 device's map moves only when a session brings new
values, so there the same check runs across sessions, not seconds.

Signal colours come from the device's RFPolicy `clientSignal`, five
levels per band, with a built-in scale where no policy sets one; the
topology response carries it as `signal_scale`. A vendor whose radio
reports signal in another encoding is mapped to dBm in its rule, never
graded on a scale of its own.

An extender that runs its own management client is a second device
with a page of its own, and the map has to be the same from both.
That is `home-links`: what ties the two together, the traps a wireless
backhaul sets, and the check to run across the fleet before and after
any change here.

## 5. The neighbour scan

Vendors without `Device.WiFi.NeighboringWiFiDiagnostic` hide the site
survey behind a vendor tree (ARRIS: `X_0000C5_Wireless.
NeighboringWiFiDiagnostic`; ASUS: `WiFi.X_ASUS_SiteSurvey`). That is
an ActionProfile, not telemetry: a scan takes the radio off channel,
so it must be operator-invoked, never polled. Three documents, and
only the right-hand sides are vendor-specific:

- A MappingTable that mirrors the left-hand side of
  `baseline/tr181/wifi-scan.yaml` (state, result count, per-result
  bssid, ssid, channel, rssi, band, bandwidth) with the vendor paths
  on the right, listed in the vendor MappingProfile in place of the
  standard scan table. Without it `canonical.wifi.scan.state`
  resolves to nothing and the action cannot start.
- An ActionProfile modelled on `platform/actions/wifi-scan-arris.yaml`:
  same `set` and `await` on the canonical, `collect` naming the vendor
  subtree, priority above the standard profile's 10, selector on the
  vendor tuple.
- A normalizer copied from `wifi-scan-arris.ts` with `ROOT` and
  `method` changed.

Verify by running the action end to end:

```bash
curl -s -X POST "$HERDER_API/api/v1/devices/<device_id>/actions/wifi_scan" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Follow the run at `GET /api/v1/action-runs/<run_id>`: DiagnosticsState
should walk Requested to Complete and the result should carry a
neighbour count. Vendor scan trees carry quirks (fields that read
"Auto" instead of numbers, radio selectors that do nothing); record
what the hardware actually did as comments in the profile, the way
the existing scan profiles do.

## USP note

On USP devices telemetry is pushed rather than polled. Events (a client
joining, a channel moving) are subscriptions, and
`baseline/usp/wifi-realtime.yaml` in the config repo is the worked
example. Values that move continuously (signal, rates, counters) are a
`sampled:` block, the agent pushing them as a Bulk Data report, as in
`baseline/usp/wifi-sampled.yaml`; a subscription on them is either
ignored or a notification every few seconds. A block that is only
worth its cost while someone is looking takes `whileWatched: true`, the
way `baseline/usp/topology-live.yaml` does for the map. The
client-table hunt, the vendor-extension search, and the idle-client
zeros trap apply unchanged.
