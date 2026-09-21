---
name: survey-datamodel
description: Discover and summarize what a CPE actually exposes. Use when starting a vendor integration, when a mapping needs a real path, when asked what a device model supports, and before telling anyone a device does not support something.
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
metadata. If the device is not there, either it has not informed yet
or Herder refused it, since Herder registers a CPE only after
authenticating it. That problem comes first; step 0 of
`onboard-vendor` covers it.

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

## 4a. Bulk data capability

TR-157 Annex A, carried as `Device.BulkData.` or
`InternetGatewayDevice.BulkData.`, has the CPE collect on a schedule and
push reports out of band instead of answering questions during a
session. Support varies enough between vendors that it has to be read
off the device.

The metrics worth collecting this way have a fixed path, no retention on
the device, and a value that moves between informs: interface counters,
CPU and memory, optical levels. A table the device already buckets and
retains, which some vendors do for per-client WiFi statistics, is better
read on demand than streamed.

Six read-only parameters at the root answer whether it is available:

```bash
curl -s "$HERDER_API/api/v1/devices/<uuid>/parameters?path=InternetGatewayDevice.BulkData." \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

- `Protocols` and `EncodingTypes`. `HTTP` with `JSON` is the workable
  pair. A device offering only `Streaming` or `File` with `XML` or `XDR`
  is an IPDR integration, which is a different job.
- `MinReportingInterval`, the floor in seconds.
- `MaxNumberOfProfiles` and `MaxNumberOfParameterReferences`, the two
  hard limits. `-1` means unlimited.
- `ParameterWildCardSupported`. Read the next section before drawing a
  conclusion from it.

Read these from the device, not from a published data model or a vendor
XML export. Those carry a `default` for the flag, and a default is what
an object is created with, not what the hardware answers.

### Wildcards and object paths are not the same thing

`Profile.{i}.Parameter.{i}.Reference` takes either a full parameter path
or an object path ending in `.`. An object path collects the whole
subtree, every instance and every contained parameter, resolved by the
CPE at collection time. That is ungated, and it works whether or not the
device supports wildcards. It is also what makes bulk data tolerate
tables whose instances come and go.

`ParameterWildCardSupported` governs only `*` in place of an instance
identifier. When it is false a reference cannot collapse an index in the
middle of a path, so `WLANConfiguration.*.AssociatedDevice.` is rejected
while `WLANConfiguration.1.AssociatedDevice.` is accepted.

So with wildcards off you can collect every parameter of a table whose
instances churn, or specific parameters at a fixed index, but not
specific parameters across a churning table.

### Which paths are safe to name

Sort every path the integration wants into three tiers:

1. **Fixed and stable across the model.** The WAN interface and its
   stats object. Name it directly.
2. **Fixed, but decided per model or firmware.** WLAN instance numbers,
   and which `WANIPConnection` instance carries the live connection.
   Stable for one identity tuple and not across tuples, so it belongs in
   the vendor's mapping tables rather than a shared profile. A profile
   that names an instance the model does not have faults 9005 every
   session and loses that data quietly.
3. **Churning.** Associated devices, hosts, anything keyed on something
   that joins and leaves. Never enumerate these.

Reference the object path at the lowest object whose parent index is
stable. Tier 2 decides how many references you write, tier 3 rides
inside them.

## Not finding it is not the same as it not being there

Nothing above reads the device. The discovered model is one walk that
happened once, the stored parameters are the union of what past
sessions happened to carry, and both are caches. A feature is missing
from them for four reasons and only one of them is the hardware: the
walk was truncated, the branch was never walked, the device has
reported the names but no values yet, or the object really is not
there.

So every feature has three states, not two:

- **present**: the object is there and you read it.
- **present but empty**: the object is there, with no instances or no
  values right now. A call log on a unit that has made no calls, an
  associated-device table at four in the morning. The feature works;
  proving it needs a unit that exercises it.
- **unknown**: you did not find it. Unknown is work outstanding, not
  a finding. It does not become "the device does not support it" in a
  summary, in a scoping answer, or in a sentence to the operator.

Moving a feature from unknown to absent takes all of this:

1. **Search the whole tree, not the branch it belongs in.** A vendor
   is as likely to keep a feature under its own prefix as under the
   standard object: a call log can be `VoiceService.{i}.CallLog.`, a
   vendor `VoipLog` table, or a `SIP.` branch the vendor added. Sweep
   every path case-insensitively, with several words for the thing
   (`call`, `cdr`, `history`, `log`), and sweep every `X_*` prefix
   step 4 turned up.
2. **Search names, not values.** A filter that keeps only parameters
   that have a value drops exactly what you are hunting: an object
   the device has named and never populated. Count names.
3. **Read what the standard calls it.** TR-104 names the voice
   objects, TR-143 the throughput diagnostics, TR-181 and TR-098 the
   rest. A branch nobody thought to search is the ordinary reason a
   feature looks missing.
4. **Confirm the walk was whole.** `truncated` set, a discovery task
   that ended `failed`, a manual walk that stopped short: none of
   them can carry a negative. Fix the walk first.
5. **Refresh the subtree live.** A `GetParameterNames` task on the
   parent object and a connection request, the mechanism from step 3a
   with one path instead of a batch. Firmware adds objects, so a tree
   walked before the last upgrade answers for the old build.
6. **Ask a unit that would have the data.** Say which one: serial or
   device id, the exact firmware string, and how many units you
   checked.

Then write the finding as what it is: not present under any branch of
the tuple, at the firmware string the unit reports, after a full walk
of N parameters refreshed on a stated date, across M units. That
sentence is worth something. "The device does not support it" is not,
because nobody can tell which of the four reasons it rests on.

A negative is scoped to a firmware string, never to a vendor. Support
arrives and leaves in firmware builds, and the same model in two
operator builds is two answers.

## 5. Summarize

Report: the tuple, parameter count and truncation, the data-model root
(`Device.` vs `InternetGatewayDevice.`), every vendor prefix found
(`X_<OUI>_`, `X_<NAME>_`) with the subtrees it appears under, and the
writable clusters relevant to the four product priorities: management
credentials, WiFi, diagnostics, firmware.

Then the feature inventory, one line per platform feature, stating
whether the device has the standard object, a vendor tree instead, or
neither, each in one of the three states above and with the evidence
behind anything called absent. It is what the operator chooses from in
`onboard-vendor`:

- Interfaces: the Ethernet interface table, and which instance has
  `Upstream=true` (TR-181) or is the WAN object (TR-098).
- Bulk data: whether `BulkData.` exists, the transports and encodings
  it advertises, the reporting floor, and whether wildcards are
  supported.
- WiFi clients: the associated-device table and where the signal leaf
  is (`SignalStrength`, or an `X_*` leaf).
- Network map: `WiFi.MultiAP`, `WiFi.DataElements`, a vendor mesh
  table, or hosts only.
- WiFi scan: `NeighboringWiFiDiagnostic`, or a vendor survey tree with
  its own `DiagnosticsState`.
- Port forwards: `NAT.PortMapping` or the TR-098 `PortMapping` table.
- Remote access: `UserInterface.RemoteAccess`, the account table the
  remote UI authenticates against, and any vendor ACL, timeout or
  status leaves.
- Diagnostics: which `IP.Diagnostics.*` and TR-143 objects exist.
- Firmware: the exact `SoftwareVersion` string as reported.

This summary is the input to the `mapping-gaps` skill and to the
scoping question.

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

Record the agent's endpoint ID. Herder reads the `oui` selector label
from it only for the schemes that carry one: `oui:<OUI>:<instance-id>`,
`os::<OUI>-<SerialNumber>` and `ops::<OUI>-<ProductClass>-<SerialNumber>`.
An agent that writes `os::` without the hyphen presents no `oui`, so a
bootstrap entry selected on `oui` does not admit it.
