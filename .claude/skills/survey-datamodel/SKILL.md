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
serves every identical CPE. Two answers are normal and good:

- `409 CONFLICT` with "discovery already pending": a walk is queued or
  running. Not an error; proceed to polling.
- A device without a working connection request path picks the task up
  on its next periodic Inform, so allow at least one inform interval
  before concluding anything failed.

## 3. Poll for the model

```bash
curl -s "$HERDER_API/api/v1/schema/models" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Wait until a row matches the tuple. Note `parameter_count` and
`truncated`: a truncated walk means the tree is bigger than what was
stored, and conclusions about "the device does not have X" are unsafe.

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
