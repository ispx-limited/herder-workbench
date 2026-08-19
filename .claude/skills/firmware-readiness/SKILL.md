---
name: firmware-readiness
description: Make a new vendor's model safe to include in firmware campaigns. Use after basic onboarding, or when asked to prepare firmware upgrades for a device family.
---

# Firmware readiness for a new model

Goal: this model can be named in a firmware campaign and behave
correctly: right image resolved, right devices skipped, one device
proven end to end before any cohort. Campaigns are documented at
https://docs.herder.ispx.co/guides/firmware-campaigns/; this is the
vendor-onboarding angle.

## 1. Identity must be exact, and it usually is not

Campaigns and image applicability select on identity labels
(`model`, `firmwareVersion`), and version skipping compares **byte
for byte** against what the device reports. Check what this vendor
actually reports:

```bash
curl -s "$HERDER_API/api/v1/devices?limit=5&search=<serial>" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

A common shape in the field: a base version with an operator-specific
suffix, `7.2.1b3405_EXAMPLE` where the vendor builds per ISP. An
image catalogued as `7.2.1b3405` against a fleet reporting the
suffixed string re-flashes on every campaign rerun. Catalogue the
exact string the device reports, and if `model` or firmware arrive
empty or mangled, fix identity first (an IdentityProfile in the
config fork) before touching campaigns.

## 2. Scope an image and prove the selector

Upload with a `device_selector` scoped to the model, then prove the
selector catches what you think, because a selector matching nothing
is the failure that reports success:

```bash
curl -s -X POST "$HERDER_API/api/v1/firmware/images/check-selector" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"device_selector": {"matchExpressions": [{"key": "model", "operator": "In", "values": ["<model>"]}]}}'
```

Verify the response's `sha256` against your own digest of the file
before going further; the fleet gets what landed on disk, not what
you meant to upload.

## 3. One device before any cohort

Run the download against a single device and watch it complete before
any campaign names the model. What to verify on a new vendor: the
device accepts the minted URL, reports the transfer complete, comes
back after the reboot, and reports the new version string, exactly.
The corners that bite are vendor-specific: some hardware wants a
`target_filename`, some rejects images by header before flashing, and
some reports the old version for one more inform after upgrading.
Whatever this vendor did, record it as comments where the next
campaign author will look.

## 4. Then a canary cohort

First real campaign: a selector no wider than a handful of devices
(a tag works), watched to completion, before the model joins fleet
campaigns. Simulated fleets cannot stand in here; flashing is the one
flow containers structurally cannot verify.
