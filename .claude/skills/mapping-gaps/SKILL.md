---
name: mapping-gaps
description: Turn a device's canonical-coverage gaps into mapping-table skeletons with candidate paths. Use after survey-datamodel, or when asked why a platform feature is dark on a device.
---

# From coverage gaps to mapping tables

Goal: for one device, the list of reserved canonicals its profile does
not bind, each with candidate raw paths from the discovered model, and
a MappingTable skeleton ready for review. Requires a surveyed model
(run `survey-datamodel` first if there is none).

## 1. Read coverage

```bash
curl -s "$HERDER_API/api/v1/devices/<device_id>/canonical-coverage" \
  -H "Authorization: Bearer $HERDER_TOKEN"
```

Grouped by feature; each canonical is `bound`, `unreported`, or
`unmapped`. Only `unmapped` is mapping work. `unreported` means the
binding exists and no value has arrived, which is normal for test
outputs; do not "fix" it.

## 2. Understand each unmapped name

Fetch the reserved registry once:

```bash
curl -s https://docs.herder.ispx.co/schemas/reserved-canonicals.json
```

It gives `valueType` (enforced at sync), `feature`, and a description
per name. A mapping entry with the wrong `valueType` rejects the whole
table.

## 3. Find candidate paths

For each unmapped canonical, search the discovered model
(`survey-datamodel` step 4) with terms from its meaning, not its
spelling: `connection_request_username` wants
`ManagementServer.ConnectionRequestUsername`; a WiFi scan state wants
the vendor's `X_*` neighbour-scan subtree. Check how the baseline
tables in herder-public-configs (`baseline/tr098/mappings.yaml`,
`baseline/tr181/mappings.yaml`) bound the same canonical for standard
trees; the vendor path is often the same shape under a vendor prefix.

Rules:

- Candidates come from the surveyed model only. No invented paths.
- Instance wildcards are `{i}`, not `*` or a literal index.
- If nothing plausible exists on the model, say so. An honest "this
  device cannot do this feature" beats a guessed binding.

## 4. Emit the skeleton

One MappingTable per concern, named `<concern>-<vendor>`:

```yaml
apiVersion: mapping.herder.io/v1alpha1
kind: MappingTable
metadata:
  name: mgmt-<vendor>
spec:
  mappings:
    - canonical: "canonical.mgmt.connection_request_username"
      valueType: string
      devicePath: "Device.ManagementServer.ConnectionRequestUsername"
```

Then a MappingProfile scoped to the vendor tuple that lists the
baseline tables that already fit plus the new ones, priority 50 or
higher. Validate every buffer (see `onboard-vendor` step 4) before
presenting it as done, and mark each candidate you are not certain of
as a question for the operator rather than silently committing it.
