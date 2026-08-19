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
instead of rewriting a working rule.

## 4. The neighbour scan

Vendors without `Device.WiFi.NeighboringWiFiDiagnostic` hide the site
survey behind a vendor tree (ARRIS: `X_0000C5_Wireless.
NeighboringWiFiDiagnostic`). That is an ActionProfile, not telemetry:
a scan takes the radio off channel, so it must be operator-invoked,
never polled. Model on the wifi-scan action profiles in the config
repo's `platform/actions/`, selector scoped to the vendor tuple, and
verify by running the action end to end:

```bash
curl -s -X POST "$HERDER_API/api/v1/devices/<device_id>/actions/wifi_scan" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' -d '{}'
```

DiagnosticsState should walk Requested to Complete and the results
table should fill. Vendor scan trees carry quirks (fields that read
"Auto" instead of numbers, radio selectors that do nothing); record
what the hardware actually did as comments in the profile, the way
the existing scan profiles do.
