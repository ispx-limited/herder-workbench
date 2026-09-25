---
name: surface-audit
description: Check every section of the device page, and the API behind it, for a model rather than a device. Use before declaring an onboarding done, or when asked why parts of a device page are empty.
---

# Auditing a model's device page

Goal: for one model, a verdict on every section the page renders and
every API behind it. Three findings come out of it, and the second and
third are the ones nobody looks for:

1. A section that should have data and does not.
2. A section on the page that can never have data, because the device
   has no such hardware.
3. A capability the device reports that no section shows.

An analogue telephone adapter made the case for writing this down. Its
page carried Interfaces, WiFi, Network map, Port forwards and Remote
access, five sections it can never fill, and no Voice section at all,
while the device was reporting a registered line with a call state. Six
findings on one unit, none of them visible from a coverage report,
because coverage answers a different question.

## Audit a model, not a device

One unit cannot tell you the difference between what the model cannot
do and what this unit has not done yet. That distinction is the whole
job, so the cohort comes first: take at least three units, and deliberately
spread them over firmware versions, uptime and time since last inform.

```bash
curl -s "$HERDER_API/api/v1/devices?limit=200" \
  -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.data[] | select(.model=="<model>")
         | "\(.id) \(.serial_number) \(.firmware_version) \(.last_contact)"'
```

A section empty on every unit is a property of the model. A section
empty on some is a property of those units, and what separates them is
the finding: a firmware string, a unit that has not informed since the
profile changed, a mesh node that has no clients today. Report the
split and what correlates with it, never a single unit's answer as the
model's.

## The sections and what is behind each

`GET /api/v1/devices/<id>/ui-profile` is what the page will render. Take
the section list from there rather than from a screenshot, then call the
API behind each one.

| Section | Read it with | Empty means |
|---------|--------------|-------------|
| Overview, Device status | `GET /api/v1/devices/<id>` and `.../canonical-coverage` | identity canonicals unbound |
| Interfaces | `POST .../canonical-resolve` with `canonical.interface.*` | no interface table for this tree |
| WiFi | `GET .../labeled-telemetry?metric=wifi.client.rssi`, `.../airtime` | no labels rule, or no clients associated |
| Network map | `GET .../topology` | no topology rule over the vendor's mesh or host table |
| Voice | `GET .../parameters?search=VoiceService` | no line provisioned, or no voice section on a device that has one |
| Port forwards | `GET .../modules/port-forwards` | no NAT table bound |
| Remote access | `GET .../modules/remote-access` | no remote-access table bound |
| Compliance | `GET .../compliance` | no CpeBinding for the vendor |
| Actions, Action log | `GET .../actions`, `.../action-runs` | no ActionProfile matches the device |
| Events | `GET .../events` | nothing, if the device has informed |
| Experience | `GET .../experience` | no scoring inputs |
| Faults | `GET .../faults` | nothing, and that is good |

Two reads carry most of the diagnosis when a section is empty:

```bash
# what the profile binds, and what it does not
curl -s "$HERDER_API/api/v1/devices/<id>/canonical-coverage" -H "Authorization: Bearer $HERDER_TOKEN"

# what the device refused to give, which looks identical from the UI
curl -s "$HERDER_API/api/v1/devices/<id>/parameters/rejected?limit=500" -H "Authorization: Bearer $HERDER_TOKEN"
```

A rejected path is a profile asking for something this firmware does not
have. It is noise in every session until the profile is trimmed, and it
is a finding in its own right even when no section depends on it.

## Classify, do not just observe

Every section gets one of four, and "empty" is not one of them:

- **Working**: the endpoint returns data on every unit in the cohort.
- **Empty, and the device has no such hardware**: a claim, with the
  evidence beside it. The ladder in `survey-datamodel` applies here in
  full. "No WiFi parameters in the cache" is not evidence; a whole walk
  of a unit on a named firmware, with the counts, is.
- **Empty, and it should not be**: the actionable list. Say which of
  the three it is: canonical unmapped, binding present but nothing
  reported, or the device refusing the path.
- **Not scoped**: the operator did not choose this feature in
  `onboard-vendor` step 3. Not a defect, and not yours to add.

## The section list itself is a finding

Two directions, and both are config, not code.

**A section that cannot work should not render.** It is a profile whose
selector is wider than the hardware it describes. An ATA picking up a
gateway's UI profile is the shape of it. Narrow the selector on the
vendor tuple, or add a profile for the device class, at priority 50 or
higher, as with any other vendor content.

**A capability with no section is the more expensive miss.** Compare what
the device reports against what the page offers:

```bash
curl -s "$HERDER_API/api/v1/devices/<id>/parameters?limit=3000" \
  -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.data[].path' | cut -d. -f1-3 | sort -u
```

A branch the device populates with nothing on the page to show it means
an operator opens the device, sees nothing relevant, and goes to the
vendor's own web UI instead. That is the failure this audit exists to
catch, and it is invisible to every check that starts from the sections.

## What to hand back

A table per model: section, verdict, evidence, and for anything not
working, which of the three causes and the config change that fixes it.
Then the same table after the change, because a fix nobody re-ran is a
claim.

Name the firmware the audit ran against. A model's answer today is a
model-and-firmware answer, and the next release moves it.
