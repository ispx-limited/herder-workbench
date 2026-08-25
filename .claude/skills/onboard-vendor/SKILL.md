---
name: onboard-vendor
description: The end-to-end workflow for integrating a new CPE vendor into Herder, from first Inform to a shipped vendors/ directory. Use when asked to onboard, integrate, or add support for a device or vendor.
---

# Onboard a vendor

Goal: a `vendors/<name>/` directory in the operator's config fork that
takes their new CPE from baseline coverage to the features the
operator asked for, every buffer validated against the live API
before it is committed. The narrative version is
https://docs.herder.ispx.co/guides/vendor-onboarding/; this is the
operational sequence.

## 0. Preconditions

`HERDER_API` and `HERDER_TOKEN` set (check; ask the operator for
whichever is missing before any call); the config fork located (ask
if unclear); the device informing. Confirm the device and record its
tuple:

```bash
curl -s "$HERDER_API/api/v1/devices?limit=5&search=<serial>" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

## 1. Survey

Run the `survey-datamodel` skill. Output: the tuple, the tree, the
vendor prefixes, the writable surface, and the feature inventory:
which standard objects the device has, and which of those things it
keeps under a vendor tree instead.

## 2. Scope with the operator

Before writing anything, put the feature inventory to the operator
as one question and let them choose. A standard tree already gets
most of this from the baseline. What the operator is choosing is
which surfaces they want proven on this vendor now, and where the
survey found a vendor tree in place of the standard object, which of
those to wire.

The menu, in the platform's words:

| Feature | On the device page | Standard objects the baseline binds | Vendor work when the survey says otherwise |
|---------|--------------------|-------------------------------------|--------------------------------------------|
| Interfaces | ports view: WAN and LAN link state, MACs, counters | `Ethernet.Interface.{i}` (TR-181), `WANDevice` and `LANEthernetInterfaceConfig` (TR-098) | an `interfaces-<vendor>` table when the WAN is not `Interface.1` or the port order differs |
| Monitoring | telemetry and dashboards | the baseline TelemetryProfiles for the data model | a TelemetryProfile for `X_*` leaves worth streaming |
| WiFi clients | per-client signal and rates, the experience score | `AccessPoint.{i}.AssociatedDevice.{i}.SignalStrength` | a labels rule when signal is an `X_*` leaf or the radio-to-band index differs |
| Network map | topology: gateway, mesh nodes, hosts | `WiFi.MultiAP`, `WiFi.DataElements`, `Hosts.Host` | a topology rule over the vendor mesh table |
| WiFi scan | neighbour survey as an operator-run action | `WiFi.NeighboringWiFiDiagnostic` | mapping table, ActionProfile and normalizer over the vendor tree |
| Port forwards | service module | `NAT.PortMapping.{i}` | a `nat-<vendor>` table when the object lives elsewhere |
| Remote access | service module: enable, port, credentials, allow-list, idle timeout | `UserInterface.RemoteAccess` (four leaves) | a `remote-access-<vendor>` table for the login and whatever ACL, timeout and live status the vendor exposes |
| Compliance | CVE feed findings and vendor advisories | nothing | a CpeBinding, and an Advisory per vendor notice |
| Firmware | campaign readiness | `DeviceInfo.SoftwareVersion`, Download | identity exactness and one device before any cohort |
| Diagnostics | ping, traceroute, DNS, speed test as actions | `IP.Diagnostics.*`, TR-143 | an ActionProfile over the vendor tree when the diagnostic lives there |

Ask: "Which of these do you want wired for this vendor now?" With a
question tool available, ask it as one multi-select question and put
the inventory finding in each option's description ("WAN is
Interface.9; the baseline maps Interface.1"). Without one, list the
menu with the findings and wait for the answer. Management
credentials and identity are not on the menu; they are always
checked. Record the answer, write only what was chosen, and list the
rest at the end as available later. Never assume "all of them".

## 3. Gaps

Run the `mapping-gaps` skill. Output: unmapped reserved canonicals
per feature with candidate paths and table skeletons. Coverage
reports the reserved vocabulary only; the operator-defined
namespaces the features above run on (`canonical.interface.*`,
`canonical.wifi.*`, `canonical.nat.*`, `canonical.mgmt.remote_access.*`)
show their gaps per feature, in the recipes below.

## 4. Write the vendor directory

In the config fork, `vendors/<name>/`, modelled on `vendors/arris/`:

- **MappingProfile** named `<vendor>-<family>`: selector on the `oui`
  label plus `productClass` values, priority 50 or higher (highest
  priority number wins), listing the baseline tables that fit plus the
  new vendor tables. **Binding is single-profile: the winning
  profile's table list is the device's complete mapping vocabulary.**
  A vendor profile that names only its own tables strips the device of
  every baseline binding, connection-request URL included; restate
  each baseline table the device still needs. On a TR-181 CWMP device
  the list to restate is `baseline-tr181-cwmp`'s, not `baseline-tr181`'s:
  the second has no `mgmt-tr181` and a USP-only reason for it. Where a
  recipe below replaces a baseline table with a vendor one, list the
  vendor table instead, never both.
- **MappingTables** from step 3 and from the recipes.
- Then, per chosen feature, the recipe.

Multi-document files are the convention: one `<vendor>.yaml` holding
the profile and its tables reads better than five fragments. Scripts
are TypeScript against `types/sdk.d.ts`; check each one the way the
config repo README describes (per file, never a repo-wide tsconfig).
Every `devicePath` comes from the survey. No invented paths.

### Interfaces

Check `Device.Ethernet.Interface.{i}.Upstream` in the survey. The
baseline `interfaces-tr181` table assumes the WAN is `Interface.1`
and `lan.{n}` is `Interface.{n+1}`; a device whose `Upstream=true`
sits elsewhere is mapped exactly wrong by it. Copy
`baseline/tr181/interfaces.yaml`, repoint the WAN entries at the
upstream interface and the LAN entries at the switch ports in the
order the device enumerates them, name it `interfaces-<vendor>`, and
list it in the profile in place of `interfaces-tr181`. TR-098 has
the split in the object names and rarely needs this.

### Monitoring

The baseline TelemetryProfiles for the data model already stream the
standard counters. A vendor TelemetryProfile carries only what the
baseline cannot: `X_*` leaves with a reader (mesh backhaul signal,
per-node state, vendor error counters), all `interval: 0` unless a
leaf only changes when polled. The exclusion list in
`vendors/arris/arris.yaml` is the rule set: nothing that is a SET,
no passphrases, no diagnostic triggers, nothing the baseline already
collects. Vendor priority beats the baseline on conflicting paths.

### WiFi clients, network map, WiFi scan

The `wifi-experience` skill owns these three. In short: a labels
rule only when signal lives under an `X_*` leaf or the band index
differs; a topology rule when the mesh lives under a vendor table;
and for the scan a vendor mapping table that mirrors
`baseline/tr181/wifi-scan.yaml`'s left-hand side, listed in the
profile in place of the standard table, with an ActionProfile whose
`collect` names the vendor subtree.

### Port forwards

The `port-forwards` module runs over `canonical.nat.port_forward`,
bound by `nat-tr181` or `nat-tr098`. A vendor table is needed only
when the mapping object is pinned somewhere the baseline does not
look (`vendors/arris/nat.yaml` pins `WANIPConnection.2`); when it
is, list the vendor table instead of the baseline one.

### Remote access

The `remote-access` module reads `canonical.mgmt.remote_access.*`.
The baseline binds `enable`, `port`, `protocol` and
`supported_protocols` from `UserInterface.RemoteAccess`. The rest is
vendor knowledge: `username` and `password` from the account the
remote UI actually authenticates (`Device.Users.User.{n}` on TR-181,
`UserInterface.User.{n}` on TR-098, or the vendor's own), and any
`acl`, `idle_timeout` and `status` leaves the survey turned up.
Model on `vendors/arris/remote-access.yaml`; it is an additional
table, listed beside the baseline one. Record what empty means for
the ACL; on the ARRIS it means allow any.

### Compliance

A `CpeBinding` (shape in `platform/compliance/compliance.yaml`)
scoped to `manufacturer` plus `productClass`, with the product's
feed identity as `cpe:2.3:o:<vendor>:<product>_firmware`, written
unescaped. The compliance role fetches a new product on the next
config reconcile and the sweep runs on every config change, so
findings appear within minutes, not at the daily refresh. Versions
compare segment by segment and a dot and an underscore both end a
segment, so a router that writes `3.0.0.4.388_23110` matches records
the feed writes as `3.0.0.4.388.23748`; any other decoration needs
`versionExtract`. A vendor notice that never became a CVE is an
`Advisory` in the same file, fenced by an exact `In` list when the
firmware strings are not semver.

### Firmware

The `firmware-readiness` skill: identity exact to the byte, a
selector proved before any image is scoped, one device before any
cohort.

### Diagnostics

`platform/actions/` already runs ping, traceroute, DNS, speed tests
and the rest through canonicals on both data models. A vendor
ActionProfile is needed only when the diagnostic lives under a
vendor tree or takes a vendor input (`vendors/arris/ping.yaml`),
and then only `collect`, `normalize` and the selector differ.

### The device page

`DeviceProfile` decides which sections the device page shows.
`baseline-tr181-ui` and `baseline-tr098-ui` already cover a standard
tree; write a vendor one only to add a section (a mesh view, a
vendor module) and, as with mapping profiles, it replaces rather than
extends, so restate every section the operator expects.

## 5. Validate every buffer

Before any commit, per document, against the domain named by the
kind's registry entry (`domain` in
https://docs.herder.ispx.co/schemas/kinds.json). Look the domain up
for every kind; never infer it from the group name. The trap that
catches everyone: `EnrichmentRule` lives in the `telemetry_enrichment`
domain, not `telemetry`, and validating against the wrong domain
reports the kind as undeclared.

```bash
curl -s -X POST "$HERDER_API/api/v1/config/mapping/validate" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"body": "<the YAML document>"}'
```

`{"ok": true, "errors": []}` or a 200 with the errors listed; a 4xx is
a transport problem, not a verdict. For a script, send the buffer with
its repo path as `name` and the file content as `body` against the
domain that owns it; the response carries the transpiler's type errors
with line and column in the message:

```bash
curl -s -X POST "$HERDER_API/api/v1/config/provisioning/validate" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "platform/provisioning/boot.ts", "body": "<the script source>"}'
```

Passing `name` matters: the validator overlays the buffer on the
stored bundle, so cross-file breakage (a YAML referencing the script,
another table colliding on a name) surfaces now instead of at sync.

One overlay limit to know: only the one buffer overlays. A document
referencing a script that is new in the same change reports the
script as missing until the source has synced both files; validate
the script buffer by itself (that works), and re-run the document's
validation after the sync. `GET /api/v1/config/sources` shows
per-domain sync status when in doubt.

## 6. Ship, verify the binding, then verify each feature

Commit, push, let the config source sync. The binding says which
profile won and why, and it is the first thing to check after the
sync:

```bash
curl -s "$HERDER_API/api/v1/devices/<device_id>/mapping-binding" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

If the baseline still binds, the usual causes are priority (highest
number wins) and a selector that does not match the device's labels.
Then re-read coverage: the mapped names report `bound`, and what is
still `unmapped` is the honest remaining list.

Then each chosen feature, against the device, not against the files:

| Feature | Proof |
|---------|-------|
| Interfaces | `POST /api/v1/devices/<id>/canonical-resolve` with `{"patterns": ["canonical.interface.wan.*", "canonical.interface.lan.*"], "include_values": true}` resolves to the upstream port's paths and values |
| Monitoring | the vendor paths appear in `GET /api/v1/devices/<id>/parameters?search=X_` after a session |
| WiFi clients | `GET /api/v1/devices/<id>/labeled-telemetry?metric=wifi.client.rssi` has rows (allow an inform cycle) |
| Network map | `GET /api/v1/devices/<id>/topology` lists the nodes and edges the mesh table describes |
| WiFi scan | `POST /api/v1/devices/<id>/actions/wifi_scan` is 202; `GET /api/v1/action-runs/<run_id>` walks to completed with a neighbour count |
| Port forwards | `GET /api/v1/devices/<id>/modules/port-forwards` resolves every field; a `POST .../collections/port_forward` with `{"fields": {...}}` lands as a new instance in the parameters |
| Remote access | `GET /api/v1/devices/<id>/modules/remote-access` resolves the fields the vendor table added |
| Compliance | `GET /api/v1/devices/<id>/compliance` lists the findings for the reported firmware after the sweep |
| Firmware | the checks in `firmware-readiness` |
| Diagnostics | the action's run reaches completed with a result |
| Device page | `GET /api/v1/devices/<id>/ui-profile` names the expected sections |

Report to the operator: what was wired, with the proof; what the
device cannot do; any candidate you were unsure of; and the menu
items left for later.

## Protocol note

Check `protocols` on the device row in step 0. The flow above is
protocol-agnostic; where CWMP and USP genuinely diverge (discovery,
task delivery, telemetry shape, firmware), the divergence is noted in
the skill that owns that stage.
