---
name: parity-gate
description: Run the parity gate against a device and work its findings to zero. Use at the end of every onboarding, before telling anyone a model is done, and whenever asked what a model is missing.
---

# The parity gate

Goal: `tools/parity.py <serial>` exits 0 for at least one unit of the
model. Until it does, the onboarding is not done, whatever was wired.

The gate exists because a checklist of the features somebody chose to
build cannot find what nobody thought of. It starts from the other end:
everything the device exposes, compared with what Herder reads, maps
and shows.

```bash
tools/parity.py <serial-or-device-id> --run-actions \
  --waivers <config-repo>/.herder/parity-waivers.json
```

It needs `HERDER_API` and `HERDER_TOKEN`, Python 3 and PyYAML.

## What it checks

| Check | A FAIL means |
|-------|--------------|
| `model` | no walked data model for the tuple, a truncated one, or one walked at an older firmware |
| `coverage` | a reserved canonical is unmapped, or mapped to a path the device has and nothing reads |
| `module` | a service module field does not resolve |
| `page` | the device page renders a canonical that does not resolve |
| `object` | the device exposes an object a platform surface uses and no telemetry profile reads it, or reads it without the leaves the surface needs |
| `map` | active hosts missing from the network map, clients without the name, address or signal the device reports, extenders without identity or backhaul, ports collapsed or named by an internal path |
| `wifi`, `experience` | associated clients with no signal rows, or no experience score |
| `faults` | a rule or script faulted on this device in the last day |
| `telemetry` | the device rejects paths a profile asks for |
| `action` | with `--run-actions`, a capability did not complete or completed with no result |
| `waiver` | a waiver that matches nothing |

"Read" means a telemetry profile that selects the device asks for the
path. A value somebody fetched once by hand does not count.

## Working a finding

Every FAIL ends one of two ways.

**Wire it.** Find the path in the walked model, read it live once to
see its real value and shape, write the mapping or telemetry line in
the config repository, validate the buffer, and evaluate it against the
device before it ships:

```bash
curl -s -X POST "$HERDER_API/api/v1/config/mapping/evaluate?device_id=<uuid>" \
  -H "Authorization: Bearer $HERDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "<repo path>", "body": "<the YAML document>"}'
```

Each canonical comes back `resolved` with the value, or `no_match`.

**Waive it.** Only when the hardware does not have the thing, or the
operator has decided against it. A waiver is a line in
`.herder/parity-waivers.json` in the config repository:

```json
{
  "waivers": [
    {
      "match": "module:remote-access.allowed_addresses",
      "reason": "the web UI has no allow-list on this firmware",
      "evidence": "FAST5599 SGNB330000074, model of 21730 parameters walked 2026-09-21, no leaf under UserInterface or RemoteAccess"
    }
  ]
}
```

`match` is `<check>:<id>` as the gate prints it, with `*` allowed. A
waiver without evidence is refused, and one that stops matching fails
the gate, so the list cannot rot. "Not yet" is not a reason. The bar
for calling something absent is the one in `survey-datamodel`.

An `object` finding for an object `tools/features.json` does not know
is usually a vendor tree. Read it before deciding: it is as likely to
be the vendor's mesh table as a debug counter.

## What the gate cannot see

It reads one unit. Run it on at least three of the model, spread over
firmware and uptime, before calling the model done; `surface-audit`
says why. It does not run actions that load the line or change the
device. It does not judge whether a value is right, only that one
resolves: read the WAN address it reports against the one you know.
