---
name: migrate-genieacs
description: Convert GenieACS provisions, presets and virtual parameters into Herder provisioning rules, scripts and mapping tables. Use when asked to convert, port or migrate a GenieACS configuration to Herder.
---

# Migrate from GenieACS

Goal: the operator's GenieACS provisions, presets, virtual parameters
and extensions, rewritten as Herder config in their fork, every buffer
validated and evaluated against a live device before it is committed.
The Herder side is documented at
https://docs.herder.ispx.co/guides/provisioning-rules/ and
https://docs.herder.ispx.co/guides/script-sdk/; the GenieACS side at
https://docs.genieacs.com/en/latest/provisions.html,
https://docs.genieacs.com/en/latest/virtual-parameters.html and
https://docs.genieacs.com/en/latest/extensions.html. Read the source
semantics there when a provision does something this skill does not
name; do not guess what a `declare()` meant.

## 0. Preconditions

`HERDER_API` and `HERDER_TOKEN` set (check; ask the operator for
whichever is missing before any call); the config fork located; the
devices the scripts target admitted to Herder and bound to a mapping
profile (run `onboard-vendor` first for hardware Herder has not seen).
A script that writes a canonical the device's profile does not bind
fails as `canonical_unmapped`, so the mapping work comes before the
script work.

The GenieACS side is read through its NBI (port 7557 by default,
reference at https://docs.genieacs.com/en/latest/api-reference.html).
Ask the operator for its address; it is normally reachable only from
inside their network. Read it the way step 1 describes, smallest
collection first, and never paste an export into the repository as
is: a provision can carry passphrases and API keys in literals.

## 1. Read GenieACS through its NBI

Every collection answers `GET /<collection>/?query=<mongo filter>`
and takes `projection=<comma-separated fields>`. Use both on every
call: a device document carries the whole data model with a
timestamp, type and writable flag per parameter, and a fleet of them
is more than any session should read. Send the query through
`--data-urlencode` so the JSON survives the URL:

```bash
G=http://<genieacs>:7557
curl -s -G "$G/presets/" > presets.json
```

Presets are the index: each one names its `precondition`, `events`,
`schedule`, `weight`, `channel` and `configurations`, and the
`configurations` entries of type `provision` name the scripts in
use. Read only those:

```bash
curl -s -G "$G/provisions/" --data-urlencode 'query={"_id": {"$in": ["inform", "wifi"]}}'
curl -s -G "$G/virtual_parameters/"
```

Virtual parameters are usually few; read them all. Extensions are
files under GenieACS's `config/ext/`, not in the NBI; ask the operator
for the ones the provisions call.

Then three reads that make the conversion cheaper and more honest:

- **Who each preset reaches.** A precondition is a MongoDB filter, so
  it is its own query. Post it back with a projection of `_id` and
  count the result; that is the population the Herder selector has to
  match, and a preset that matches nothing is not worth porting.

  ```bash
  curl -s -G "$G/devices/" --data-urlencode 'query={"_tags": "managed-wifi"}' \
    --data-urlencode 'projection=_id,_deviceId._OUI,_deviceId._ProductClass' | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(len(d)); print(sorted({(x["_deviceId"]["_OUI"], x["_deviceId"]["_ProductClass"]) for x in d}))'
  ```

  The OUI and product class pairs are the Herder selector, on the
  same labels.
- **What a provision's paths look like on real hardware.** Project
  the paths a script writes on one device from each pair; the answer
  says whether the path exists, what it holds now and whether it is
  `_writable`. That is the survey for this skill: a `devicePath` in a
  mapping entry comes from here or from `survey-datamodel`, never from
  the provision alone.

  ```bash
  curl -s -G "$G/devices/" --data-urlencode 'query={"_id": "<device_id>"}' \
    --data-urlencode 'projection=Device.WiFi.SSID.1.SSID,Device.WiFi.AccessPoint.1.Security.KeyPassphrase'
  ```

  Keep the current values: step 5 compares them with what Herder's
  evaluate reports as `from`. A projected passphrase is still a
  secret; it stays in the session and out of the fork and the report.

  An empty projection is not proof the path is missing. A device
  document holds every parameter GenieACS has discovered, and one the
  CPE has named but never valued carries `_object` and `_writable`
  with no `_value`, so any read that keeps only values drops exactly
  the object you are hunting. Project the parent and look at the keys.
  When the answer decides anything, refresh it first and read again:

  ```bash
  curl -s -X POST "$G/devices/<device_id>/tasks?connection_request" \
    -H 'Content-Type: application/json' \
    -d '{"name": "refreshObject", "objectName": "Device.Services."}'
  ```

  The document is what past sessions happened to carry, not what the
  device has now. `survey-datamodel`'s section on absence is the same
  rule on the Herder side.
- **What is broken today.** `GET /faults/` lists the presets that
  fail on some device, with the fault id as `<device_id>:<channel>`.
  A provision that faults on half its population is not ported as is;
  it is a question for the operator.

Firmware provisions name a file; `GET /files/` carries each file's
`fileType`, `oui`, `productClass` and `version` metadata, and the
version is what a Herder release is named by. Hand that list to
`firmware-readiness`.

## 2. The model shift

GenieACS runs a provision repeatedly inside one session until its
declarations produce no further side effects; its docs say so, and
scripts written for it tend to lean on `declare()` timestamps to get
freshness and on `commit()` to order writes.

Herder's replay model is stricter, and every conversion has to be
written for it:

- **The script re-runs from the top.** `device.fetch()` returns `null`
  on the first pass and queues a live read. When the answer arrives the
  whole script runs again, up to eight passes per evaluation. Nothing
  before the fetch is remembered between passes.
- **It runs again on every matching session.** Boot, periodic,
  connection request: each one is a fresh evaluation. A value derived
  differently on each run (`Math.random()`, `Date.now()` in a written
  value) never matches reported state and becomes a permanent write
  loop across the fleet.
- **Freshness is per session, not per second.** A value the session's
  Inform carried, or an earlier fetch in this evaluation, answers from
  cache; anything else is a round trip. There is no equivalent of
  `{value: Date.now() - 3600000}` meaning "at most an hour old".
- **Fetches in one pass batch into one round trip.** A fetch whose path
  depends on another fetch's answer costs a round trip per level.
- **Herder diffs before it writes.** Desired state is compared with
  what the device is known to hold, and only the difference is sent. A
  script does not need to read a parameter before setting it, and a
  device already in the desired state produces no task.
- **Nothing is applied until the script finishes.** A throw drops the
  whole queue; `provision.skip()` drops it cleanly with a reason.

So every converted script:

1. **Hoists** every independent fetch and every `provision.call()` to
   the top, before any branch.
2. **Flattens** fetch chains: fetch the parent object once with a
   wildcard or a search path rather than fetching an index and then a
   leaf under it.
3. **Derives** rather than generates. Anything per-device comes from
   `device.oui`, `device.serialNumber` or a hash of them, never from
   randomness or the clock.
4. **Assumes nothing about one run.** No counters, no "first time"
   flags in local variables, no ordering between two rules. Tags are
   the only memory a script has, and they are idempotent.

## 3. Concept map

| GenieACS | Herder | Notes |
|----------|--------|-------|
| `declare(path, {value: ts})` (read) | `device.get(path)` for what the session already knows; `device.fetch(path)` for a live read | The timestamp goes away. Fetch when the decision needs the device's current value, get when the Inform or cache is enough. |
| `declare(path, null, {value: v})` (write) | `device.set(canonical, v)` | Herder diffs, so no read-before-write. Canonical names keep the rule vendor-neutral. |
| `declare(path, {value: now}, {value: v})` (refresh then write) | `device.set(canonical, v)` | The refresh was GenieACS's way to get the diff; Herder already has it. |
| Alias filter `Obj.[Key:value].Leaf` | Search path `Obj.[Key=="value"].Leaf` in `get` or `fetch` | Terms are `==` joined by `&&`. |
| `declare("Obj.*", null, {path: 1})` (ensure an instance) | `device.ensureObject('Obj.[Key=="v"]', { ...params })` | The terms seed the new instance, so identity is written once. |
| `declare("Obj.[]", null, {path: 0})` or `{path: 0}` on a filter | `device.removeObject(pathOrSearch)` | Removes every match. |
| `clear(path, ts)` | Nothing | Herder forgets applied state on factory reset by itself. |
| `commit()` | Nothing | Writes apply after the script, as one batch. |
| Preset `configurations` of type `value` | `desired.parameters`, a flat map of canonical name to value | No script. A value-only preset is the declarative rule form. |
| Preset `configurations` of type `add_object` or `delete_object` | `device.ensureObject` or `device.removeObject` in a script | The declarative form has no object operations. |
| Preset `configurations` of type `provision` | `desired.script` | One rule per preset; the script is the ported provision. |
| Preset `events` (`0 BOOTSTRAP`, `1 BOOT`, `2 PERIODIC`) | `spec.triggers`: `first_contact`, `boot`, `periodic`; add `connection_request` where a periodic rule should also run on operator contact | A rule fires on any listed trigger. |
| Preset `precondition` on `_tags`, `DeviceID.*`, or identity | `spec.deviceSelector` on labels: `tag:<name>` with `Exists`, `oui`, `model`, `productClass`, `firmwareVersion` with `SemverRange` | Labels are listed at https://docs.herder.ispx.co/guides/device-selectors/. |
| Preset `precondition` on any other parameter value | A guard in the script: `device.get`, then `provision.skip("why")` | Selectors see labels only. |
| Preset `weight` | `spec.priority`, highest first | Each rule's outputs apply independently. Two presets that wrote the same parameter need one rule, not two priorities. |
| Preset `channel` | Nothing | Every rule applies and fails independently; there is no channel to isolate. |
| Preset `schedule` | No schedule field on a rule | A time window belongs to the operation: a firmware campaign, or a fleet job with a cron expression. Do not gate a script on the clock. |
| `args` from `provisionArgs` | The rule's `config` block, read with `ctx.configGet(key, default)` | One script, many rules, different config per rule. |
| A provision calling another | `provision.run("lib/helper.ts", ...args)`; the helper reads `provision.args` | Resolves beside the caller first. |
| `declare("Tags.x", null, {value: true})` | `device.addTag("x")`; `false` is `removeTag`; a read is `hasTag` | Idempotent; `tag:x` then works in selectors. Groups: `addToGroup`, `inGroup`. |
| `declare("Reboot", null, {value: ts})` | `device.reboot({ reason: "why" })` | Keyed on its cause: one cause is carried out once a day, however many passes and sessions run. Omitted, the cause is the rule name. |
| `declare("FactoryReset", null, {value: ts})` | `device.factoryReset({ reason: "why" })` | Same bounding. Confirm with the operator before porting a reset at all. |
| `Downloads.[FileType:1 Firmware Upgrade Image]` | `device.upgradeFirmware("<version>", { reason, delaySeconds })` | Names a catalog release, never a URL. `firmware-readiness` owns getting the image into the catalog. |
| `Downloads.[FileType:3 Vendor Configuration File]` | Nothing in a script | Tell the operator; config-file pushes are not a provisioning action. |
| `ext("file", "fn", args)` | An `ExternalService` document plus `provision.call("<name>", { path, query, body })` | Herder makes the request; the script never sees a credential. Cache is mandatory. |
| `log(msg)` | `provision.log(msg)`, `provision.warn(msg)` | Capped per evaluation. |
| `DeviceID.SerialNumber`, `.ProductClass`, `.OUI`, `.Manufacturer` | `device.serialNumber`, `device.model`, `device.oui`, `device.manufacturer` | Already in scope, no fetch. |
| Virtual parameter (read side) | A `MappingTable` entry: a plain binding when the script only aliased one path, a `type: computed` entry with a script when it chose between paths | Reserved canonicals cannot be computed. |
| Virtual parameter (write side, `args[1].value`) | The same binding: a `device.set` on the canonical writes the bound `devicePath` | A vparam that wrote two data models becomes one canonical bound differently by two profiles. |
| Raw `InternetGatewayDevice.*` and `Device.*` branches in one script | One canonical name; the mapping profile picks the raw path per data model | This is the usual halving of a GenieACS script. |

Raw paths still work in `get`, `fetch` and `set`, and sometimes the
honest conversion of a vendor-specific provision keeps them. A rule
that names raw paths is data-model-bound, so give it a `dataModel:`
or `oui` selector that says so.

## 4. Workflow

### Inventory

Before converting anything, one table for the operator, from the
exports:

| Preset | Events | Precondition | Devices matched | Configurations or provision, args | Writes | Reads | ext calls | Reboot or reset | Faults |
|--------|--------|--------------|-----------------|-----------------------------------|--------|-------|-----------|-----------------|--------|

"Devices matched" and "Faults" come from step 1's reads. Plus one
row per virtual parameter (what it reads, whether it is writable, what
writes it) and one per extension function (what it fetches, from
where, with what credential).

### Scope with the operator

Put the inventory to them and ask what still needs to exist. Three
things usually drop out:

- **What the shipped seed rules already do.** `seed-first-contact`,
  `seed-boot` and `seed-periodic` in herder-public-configs derive
  connection request credentials from identity and spread the periodic
  inform interval across the fleet. A GenieACS inform preset is
  almost always replaced by them, not ported. Port it only when the
  operator has a reason the seed's pacing does not meet, and then as a
  `config` override on their own rule.
- **Refresh-only provisions.** A provision whose only job was
  `declare("Device.", {path: now, value: now})` to keep the data model
  warm is telemetry in Herder, not provisioning. Point the operator at
  TelemetryProfiles.
- **Anything that carried a secret in its source.** A passphrase or
  API key in a provision literal becomes a credential in the store or
  an ExternalService lookup. It never lands in the fork.

Record the answer and convert only what was chosen.

### Convert, in dependency order

1. **Virtual parameters** become mapping entries in a table under
   `vendors/<name>/` or, for a vendor-neutral computed value, the
   operator's own table listed by their profile. Scripts depend on
   these names, so they come first. Every `devicePath` comes from the
   surveyed model, as in `mapping-gaps`.
2. **Extensions** become `ExternalService` documents. Shape and field
   meanings are the schema at
   https://docs.herder.ispx.co/schemas/externalservice.schema.json:
   `baseUrl`, `auth.credential` as a name in the credential store or
   an `EXTSVC_CRED_<NAME>` variable on the provisioning role, a
   `timeout`, and a mandatory `cache` with a `ttl` and a `key` naming
   the identity fields that scope it (`[device.serialNumber]` for a
   per-subscriber lookup, empty for a catalog the fleet shares).
3. **Presets** become `ProvisioningRule` documents, one per concern,
   and **provisions** become the `.ts` script each rule names. Scripts
   are TypeScript against `types/sdk.d.ts` in the config fork, strict,
   wrapped in an IIFE, no imports.

Then step 5, before any commit.

## Worked examples

### A periodic inform setter

GenieACS: preset `inform` on `0 BOOTSTRAP`, `1 BOOT` and
`2 PERIODIC`, precondition `{}`, provision `inform` with args
`[300]`:

```js
const now = Date.now();
const interval = args[0] || 300;
declare("InternetGatewayDevice.ManagementServer.PeriodicInformEnable", {value: now}, {value: true});
declare("InternetGatewayDevice.ManagementServer.PeriodicInformInterval", {value: now}, {value: interval});
declare("Device.ManagementServer.PeriodicInformEnable", {value: now}, {value: true});
declare("Device.ManagementServer.PeriodicInformInterval", {value: now}, {value: interval});
```

First answer: `seed-periodic` already does this, spread across the
fleet, and a fixed interval on every device is the inform storm the
seed exists to prevent. Offer the seed. When the operator wants a
fixed value regardless, the port is one rule and one script. The two
data-model branches collapse into one canonical, the refresh
timestamps go away because Herder diffs, and the argument moves into
the rule's `config`. Had the preset carried the same two writes as
`value` configurations instead of a provision, the whole port would
be the rule with `desired.parameters` and no script at all:

```yaml
apiVersion: provisioning.herder.io/v1alpha1
kind: ProvisioningRule
metadata:
  name: inform-fixed
spec:
  triggers:
    - first_contact
    - boot
    - periodic
  # PeriodicInform lives under ManagementServer, which a TR-369 agent
  # does not implement, so this rule is CWMP only.
  deviceSelector:
    matchExpressions:
      - key: "protocol:cwmp"
        operator: Exists
  config:
    informInterval: 300
  desired:
    script: "inform_fixed.ts"
  priority: 60
  enabled: true
```

```typescript
// inform_fixed.ts: a fixed periodic inform interval from the rule's config.
// Triggered on: first_contact, boot, periodic
(function () {
  const interval = ctx.configGet("informInterval", 300);
  device.set("canonical.mgmt.periodic_inform_enable", true);
  device.set("canonical.mgmt.periodic_inform_interval", interval);
})();
```

Priority 60 puts it above the seed at 50; both still run, so tell the
operator to disable `seed-periodic` in their fork rather than rely on
ordering between two rules writing the same parameter.

### A WiFi credential push

GenieACS: preset `wifi` on `0 BOOTSTRAP` and `1 BOOT`, precondition
`{"_tags": "managed-wifi"}`, provision:

```js
const serial = declare("DeviceID.SerialNumber", {value: 1}).value[0];
const creds = ext("crm", "wifi", serial);   // {ssid, passphrase} from the OSS
if (creds) {
  declare("Device.WiFi.SSID.1.SSID", {value: Date.now()}, {value: creds.ssid});
  declare("Device.WiFi.AccessPoint.1.Security.KeyPassphrase", {value: Date.now()}, {value: creds.passphrase});
  declare("Tags.wifi-provisioned", null, {value: true});
}
```

Three documents. The extension becomes a declared service; the
credential is a name, never a value:

```yaml
apiVersion: provisioning.herder.io/v1alpha1
kind: ExternalService
metadata:
  name: crm
spec:
  baseUrl: https://oss.example.net/api
  auth:
    credential: crm-api
    prefix: "Bearer "
  timeout: 2s
  cache:
    ttl: 10m
    key:
      - device.serialNumber
```

The baseline binds `canonical.wifi.ssid.1.ssid` but no passphrase, so
the vendor table adds one. The path is the one the survey reported
for this hardware:

```yaml
apiVersion: mapping.herder.io/v1alpha1
kind: MappingTable
metadata:
  name: wifi-credentials-<vendor>
spec:
  mappings:
    - canonical: canonical.wifi.ap.1.passphrase
      valueType: string
      devicePath: Device.WiFi.AccessPoint.1.Security.KeyPassphrase
```

List it in the vendor's MappingProfile beside the baseline tables.
Then the rule, with the tag precondition as a selector:

```yaml
apiVersion: provisioning.herder.io/v1alpha1
kind: ProvisioningRule
metadata:
  name: wifi-credentials
spec:
  triggers:
    - first_contact
    - boot
  deviceSelector:
    matchExpressions:
      - key: "tag:managed-wifi"
        operator: Exists
  desired:
    script: "wifi_credentials.ts"
  priority: 50
  enabled: true
```

```typescript
// wifi_credentials.ts: SSID and passphrase from the OSS, by serial.
// Triggered on: first_contact, boot
(function () {
  // Hoisted: the call is cached per serial for the service's ttl, so
  // every replay pass after the first answers from cache.
  const creds = provision.call("crm", {
    path: "/wifi",
    query: { serial: device.serialNumber || "" },
  }) as { ssid?: string; passphrase?: string } | null;

  if (creds === null || !creds.ssid || !creds.passphrase) {
    // A dead OSS degrades this rule, not the session.
    provision.warn("crm returned no wifi credentials; leaving wifi unchanged");
    return;
  }

  device.set("canonical.wifi.ssid.1.ssid", creds.ssid);
  device.set("canonical.wifi.ap.1.passphrase", creds.passphrase);
  device.addTag("wifi-provisioned");
})();
```

The `if (creds)` guard survives, but as a warning and a return, not a
skip: `provision.skip` would also drop anything a helper had staged.
Nothing logs the passphrase.

### A conditional firmware step

GenieACS: preset `fw-xg1` on `1 BOOT`, precondition
`{"DeviceID.ProductClass": "XG-1"}`, provision:

```js
const fw = declare("Device.DeviceInfo.SoftwareVersion", {value: Date.now()}).value[0];
if (fw < "2.4.0") {
  declare("Downloads.[FileType:1 Firmware Upgrade Image]", {path: 1}, {path: 1});
  declare("Downloads.[FileType:1 Firmware Upgrade Image].FileName", {value: 1}, {value: "xg1-2.4.0.bin"});
  declare("Downloads.[FileType:1 Firmware Upgrade Image].Download", {value: 1}, {value: Date.now()});
}
```

The version test was a string comparison, which is wrong for
`2.10.0`; it moves into the selector as a semver range. The product
class moves into the selector too. The download becomes a catalog
release: `GET /files/` on the NBI gives `xg1-2.4.0.bin` its `version`,
`oui` and `productClass`, and that image has to be in Herder's
firmware catalog under that version with a selector that matches this
model first, which is the `firmware-readiness` skill, one device
proven before any cohort.

```yaml
apiVersion: provisioning.herder.io/v1alpha1
kind: ProvisioningRule
metadata:
  name: fw-xg1-2-4
spec:
  triggers:
    - boot
  deviceSelector:
    matchExpressions:
      - key: model
        operator: In
        values: ["XG-1"]
      - key: firmwareVersion
        operator: SemverRange
        values: ["<2.4.0"]
  desired:
    script: "fw_xg1.ts"
  priority: 50
  enabled: true
```

```typescript
// fw_xg1.ts: move XG-1 units below 2.4.0 onto the 2.4.0 release.
// Triggered on: boot
(function () {
  // The selector already proved model and version; the script only
  // names the release. A unit already on it, or one no catalog image
  // applies to, is skipped rather than failed, and the cause defaults
  // to the version, so replay passes and repeat boots produce one
  // upgrade, not one per session.
  device.upgradeFirmware("2.4.0", { reason: "xg1-2.4.0-rollout" });
})();
```

Say to the operator that a rule is the wrong tool for a fleet-wide
upgrade: a firmware campaign has a window, a cohort and a completion
report, and the rule form is for the "this unit must never be below
X" case. `firmwareVersion` with `In` fails closed while identity is
unresolved, so a unit that just bootstrapped may not match until its
next boot; that is the correct behaviour for a service-interrupting
step.

## 5. Validate and evaluate every buffer

Before any commit, the same calls as `onboard-vendor` step 5, against
the domain named by the kind's registry entry in
https://docs.herder.ispx.co/schemas/kinds.json: `ProvisioningRule`
and `ExternalService` are `provisioning`, `MappingTable` is
`mapping`.

```bash
curl -s -X POST "$HERDER_API/api/v1/config/provisioning/validate" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "vendors/<name>/wifi_credentials.yaml", "body": "<the YAML document>"}'
```

A script validates with its repo path as `name` and its source as
`body`; the response carries the transpiler's type errors with line
and column. A rule that references a script new in the same change
reports the script as missing until both have synced; validate the
script alone, and re-run the rule's validation after the sync.

Then evaluate, which a validator cannot do: run the rule against a
real device at the trigger it will fire on and read the plan.

```bash
curl -s -X POST "$HERDER_API/api/v1/config/provisioning/evaluate?device_id=<device_id>&trigger=boot" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "vendors/<name>/wifi_credentials.yaml", "body": "<the rule YAML>"}'
```

What to check in the response:

- `matches` is true. False means the selector does not match the
  device's labels; read `GET /api/v1/devices/<id>` and compare. The
  plan is still returned, with a `note` saying so, which is how a
  tag-gated rule is proven against a live device without tagging it.
- `changeSet.parameters` lists each write with `path`, `value` and
  `from`. `from` should equal what step 1's projected read returned
  for the same device; a difference means Herder has not yet seen the
  parameter, or the mapping points somewhere else. An empty list on a
  device already in the desired state is correct, not a failure. A
  `canonical_unmapped` error means the mapping step was skipped.
- `changeSet.tagAdds` and `tagRemoves` are the tags you expected.
- `lifecycle` carries any reboot, reset or upgrade intent, with its
  cause.
- `fetches` lists every `device.fetch()`; in evaluate mode they answer
  from what the device has already reported, so a fetch of a path the
  device has never sent stays null. Evaluate against a device that has
  informed since the mapping synced.
- `warnings` and `logs` show `configGet` keys that fell back to their
  default and what the script logged.

Evaluate is a preview; nothing reaches the device. Do it at every
trigger the rule lists, then commit, push, let the source sync, and
prove the rule on one device: a connection request, then the written
parameters in `GET /api/v1/devices/<id>/parameters?search=<path>`.
Repeat the evaluation on a second session of the same device and
confirm the plan is empty: that is the replay model working, and it is
the check a GenieACS script never had.

Report to the operator: what was ported, with the evaluation per rule;
what was replaced by the seed rules; what was dropped and why; and any
`devicePath` you were not certain of.
