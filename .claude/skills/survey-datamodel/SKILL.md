---
name: survey-datamodel
description: Discover and summarize what a CPE actually exposes. Use when starting a vendor integration, when a mapping needs a real path, or when asked what a device model supports.
---

# Survey a device's data model

Goal: a discovered, browsable parameter tree for the device's identity
tuple (OUI, product class, firmware), and a summary of what matters for
integration. All calls use `$HERDER_API` with
`Authorization: Bearer $HERDER_TOKEN`.

## 1. Find the device

```bash
curl -s "$HERDER_API/api/v1/devices?limit=5&search=<serial-or-model>" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Record `device_id`, `oui`, `product_class`, and the firmware from
metadata. If the device is not there, it has not informed yet; that
problem comes first.

## 2. Trigger discovery

```bash
curl -s -X POST "$HERDER_API/api/v1/schema/discover" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"device_id": "<uuid>"}'
```

Discovery is scoped to the identity tuple, not the device: one walk
serves every identical CPE. A `409 CONFLICT` with "discovery already
pending" means a walk is queued or running; not an error.

## 3. Watch the task, not just the models list

Discovery is delivered as a `GetParameterNames` task, and the task is
where the truth is. Follow it, and kick a session rather than waiting
out the inform interval:

```bash
curl -s "$HERDER_API/api/v1/tasks?device_id=<uuid>&limit=5" \
  -H "Authorization: Bearer $HERDER_TOKEN"
curl -s -X POST "$HERDER_API/api/v1/devices/<uuid>/connection-request" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Task states: `pending` waits for a session, `dispatched` means a
connection request went out, `completed` means the model should appear
in `/api/v1/schema/models` shortly. A device whose connection request
path does not work picks tasks up on its next periodic Inform instead,
so budget at least one inform interval. Poll with a background
until-loop, never with long sleeps.

When the model lands, note `parameter_count` and `truncated`: a
truncated walk means the tree is bigger than what was stored, and
conclusions about "the device does not have X" are unsafe.

## 3a. When discovery fails: walk it yourself

Some real CPEs refuse or silently drop a full-root
`GetParameterNames`; large trees (10,000+ parameters) are where it
happens. The symptom is the discovery task ending `failed` with a
recovery-budget error while smaller tasks against the same device
complete fine. The fallback is a level-by-level walk you drive through
the tasks API: create `GetParameterNames` tasks per object path with
`next_level` semantics, batch them (25 or so), fire one
connection-request per batch, and accumulate paths and writability
until no unwalked objects remain. A 14,000-parameter tree walks this
way in minutes. Keep the result as your survey artifact; the stored
device parameters from normal sessions supplement it.

## 4. Browse what matters

Vendor extensions, the reason this vendor needs its own tables:

```bash
curl -s "$HERDER_API/api/v1/schema/models/<model_id>/parameters?search=X_&limit=200" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Writable surface under a subtree (provisioning targets):

```bash
curl -s "$HERDER_API/api/v1/schema/models/<model_id>/parameters?prefix=Device.WiFi.&writable=true" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Paging is by cursor: pass the last `path` of a page as `cursor` to
continue. There is also fleet-wide autocomplete across all models:

```bash
curl -s "$HERDER_API/api/v1/schema/parameters/suggest?prefix=Device.WiFi.SSID&limit=25" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

`writable` in suggest results is true if the path is writable on any
known model.

## 5. Summarize

Report: the tuple, parameter count and truncation, the data-model root
(`Device.` vs `InternetGatewayDevice.`), every vendor prefix found
(`X_<OUI>_`, `X_<NAME>_`) with the subtrees it appears under, and the
writable clusters relevant to the four product priorities: management
credentials, WiFi, diagnostics, firmware. This summary is the input to
the `mapping-gaps` skill.

## USP devices are surveyed differently

Everything above is the CWMP path. A device with `protocols: {usp}`
needs none of it: the MTP session (MQTT or WebSocket) is persistent
and bidirectional, so there are no connection requests, no inform
intervals to wait out, and tasks deliver immediately. Discovery is
native: the agent itself reports its supported data model
(GetSupportedDM), so the manual walk fallback is a CWMP concern that
does not arise. USP trees are always `Device.*` (TR-181). These notes
are grounded in the platform's USP dispatch, not yet in a live
workbench run; treat them as the map, not the territory.
