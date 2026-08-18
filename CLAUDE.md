# herder-workbench

You are helping integrate a new CPE vendor into Herder, an ACS for
TR-069 and TR-369 fleets. This file is your context; the skills under
`.claude/skills/` are the workflows. Everything you need is public: the
guides at https://docs.herder.ispx.co/, the JSON Schemas and registry
metadata at https://docs.herder.ispx.co/schemas/, and the shipped
config content at https://github.com/ispx-limited/herder-public-configs.

## Vocabulary

Load-bearing terms, used consistently:

- A **CPE** is the ISP-managed gateway in a subscriber's home.
- A **subscriber** is the ISP's end customer. Never "user".
- The **user** is the ISP operating Herder.
- **Raw paths** are what the CPE reports (`Device.*`,
  `InternetGatewayDevice.*`, vendor `X_*`). Storage is always raw.
- **Canonical names** (`canonical.mgmt.connection_request_url`) are an
  additive alias layer resolved through mapping profiles, never a
  replacement for raw storage.

## Environment

- `HERDER_API`: base URL of the Herder API, e.g. `https://acs.example.net`.
- `HERDER_TOKEN`: an API token; send it as `Authorization: Bearer`.
- The operator's config repository: a fork of herder-public-configs,
  usually checked out as a sibling of this directory. Ask where it is
  if you cannot find it.

## The flow

An unknown CPE that informs already works partially: baseline profiles
resolve standard paths. Integration is closing the gap, and it is
config work in the operator's Git fork, never a code change:

1. **Survey** what the device exposes (skill: `survey-datamodel`).
2. **Read the gaps**: which reserved canonicals are unmapped, per
   feature (skill: `mapping-gaps`).
3. **Write** the `vendors/<name>/` directory: a MappingProfile scoped
   to the vendor tuple, MappingTables binding canonicals to the vendor
   paths, a TelemetryProfile for what is worth streaming, optionally a
   DeviceProfile and provisioning. Model on `vendors/arris/` in
   herder-public-configs.
4. **Validate every buffer** through the API before committing (the
   `onboard-vendor` skill has the exact calls). Scripts are TypeScript
   against `types/sdk.d.ts` in the config repo.
5. **Ship by Git** and verify coverage flipped to bound.

The full narrative is the Vendor Onboarding guide:
https://docs.herder.ispx.co/guides/vendor-onboarding/

## Rules

- Never invent parameter paths. Every `devicePath` you write must come
  from the surveyed model or the device's reported parameters.
- Respect `writable` from the survey: a read-only parameter is not a
  provisioning target, whatever its name suggests.
- Baseline files stay vendor-neutral; vendor behaviour goes in
  `vendors/<name>/` with a selector narrow enough not to capture other
  hardware, at priority 50 or higher.
- Validate before you commit. A buffer the API rejects is not done.
- Reserved canonical semantics come from
  https://docs.herder.ispx.co/schemas/reserved-canonicals.json; the
  `valueType` there is enforced at sync time.
