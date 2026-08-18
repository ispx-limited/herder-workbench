---
name: onboard-vendor
description: The end-to-end workflow for integrating a new CPE vendor into Herder, from first Inform to a shipped vendors/ directory. Use when asked to onboard, integrate, or add support for a device or vendor.
---

# Onboard a vendor

Goal: a `vendors/<name>/` directory in the operator's config fork that
takes their new CPE from baseline coverage to bound features, every
buffer validated against the live API before it is committed. The
narrative version is
https://docs.herder.ispx.co/guides/vendor-onboarding/; this is the
operational sequence.

## 0. Preconditions

`HERDER_API` and `HERDER_TOKEN` set; the config fork located (ask if
unclear); the device informing. Confirm the device and record its
tuple:

```bash
curl -s "$HERDER_API/api/v1/devices?limit=5&search=<serial>" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

## 1. Survey

Run the `survey-datamodel` skill. Output: the tuple, the tree, the
vendor prefixes, the writable surface.

## 2. Gaps

Run the `mapping-gaps` skill. Output: unmapped canonicals per feature
with candidate paths and table skeletons.

## 3. Write the vendor directory

In the config fork, `vendors/<name>/`, modelled on `vendors/arris/`:

- **MappingProfile** named `<vendor>-<family>`: selector on the `oui`
  label plus `productClass` values, priority 50 or higher, listing the
  baseline tables that fit plus the new vendor tables.
- **MappingTables** from step 2.
- **TelemetryProfile** for vendor parameters worth streaming
  (per-client RSSI extensions, mesh trees). Vendor priority beats the
  baseline on conflicting paths.
- Optional: DeviceProfile, provisioning rules, enrichment scripts.
  Scripts are TypeScript against `types/sdk.d.ts`; check each one the
  way the config repo README describes (per file, never a repo-wide
  tsconfig).

Multi-document files are the convention: one `<vendor>.yaml` holding
the profile and its tables reads better than five fragments.

## 4. Validate every buffer

Before any commit, per document, against the domain named by the
kind's registry entry (`domain` in
https://docs.herder.ispx.co/schemas/kinds.json):

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

## 5. Ship and verify

Commit, push, let the config source sync. Then re-read coverage for a
device of the tuple:

```bash
curl -s "$HERDER_API/api/v1/devices/<device_id>/canonical-coverage" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

The mapped names report `bound`. What is still `unmapped` is the
honest remaining list; shipping incrementally is the intended shape.
Report to the operator: what was bound, what the device cannot do, and
any candidate you were unsure of.
