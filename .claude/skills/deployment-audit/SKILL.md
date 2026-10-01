---
name: deployment-audit
description: Audit a running Herder deployment across its whole fleet, every model it manages, rather than one vendor. Use when taking over an existing deployment, before calling a deployment's onboarding complete, or when asked what a deployment is missing compared with another.
---

# Auditing a deployment

Goal: for every model in one deployment, what works, what does not and
why, plus the deployment-wide faults no per-model view shows. The
per-model half is `surface-audit` run across the fleet; the rest is
what a vendor onboarding never looks at: where the config came from,
whether the desk's tools succeed, whether the ACS can reach the units at
all, and whether the firmware catalogue and advisories are aimed at the
devices they were written for.

Two rules for the whole audit:

- **Keep shapes, not data.** Record profile names, counts, parameter
  path prefixes and statuses. Never write client MACs, SSIDs, parameter
  values or topology contents to disk: they are the subscribers'.
- **Read before you conclude.** A capability missing from a summary is
  often a field you did not print. Check the response shape before
  reporting an absence.

## 1. The fleet

```bash
curl -s "$HERDER_API/api/v1/devices?limit=500" -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.data[] | [.manufacturer, .product_class, .firmware_version, (.data_models|join("+"))] | @tsv' \
| sort | uniq -c | sort -rn
```

Every row is a cohort for the steps below. Follow `.next` when the count
exceeds the page.

## 2. Where the config comes from

```bash
curl -s "$HERDER_API/api/v1/config/sources" -H "Authorization: Bearer $HERDER_TOKEN"
curl -s "$HERDER_API/api/v1/config/entries?include_assets=true" -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.[] | [.source_type, .kind, .name, .file_path] | @tsv' | sort
```

Findings to look for:

- **Entries with `source_type: upload`.** Config put in through the API
  rather than Git: no review, no history, lost if the database is. Each
  one belongs in the operator's config repository. Read each in full
  (`GET /api/v1/config/entries/<group>/<kind>/<name>`, and for a script
  the `assets.herder.io` group with the path URL-encoded) before moving
  it: hand-written rules often carry a fix a shipped recipe now does
  better.
- **No source of the operator's own.** A deployment running only the
  shipped content has no place for its own rules; the first finding is
  the repository it needs.
- **Recipes loaded but disabled.** A recipe the operator needs (XMPP for
  units behind NAT is the common one) is adopted by copying it into
  their repository, not by enabling it where it ships.

## 3. Each model's page

Run `surface-audit` for every model from step 1, at least three units
each where the fleet has them. Across a whole fleet it pays to collect,
per unit, only:

- `ui-profile` → `profile_name` and the section keys
- `mapping-binding` → the bound profile
- `canonical-coverage` → per feature, counts by status
- `parameters/rejected` → the paths, with instance numbers folded to `{i}`
- counts from `topology`, `labeled-telemetry?metric=wifi.client.rssi`,
  `compliance`, `faults`, `events`
- `parameters?limit=5000` → the count and the first three path segments

Two patterns only show across a fleet:

- **Units with a few dozen parameters** among siblings with hundreds
  have never been walked. That is almost always reachability (step 5),
  not the model.
- **One page for every unit of a model** where the model plays two
  roles (gateway and mesh extender under one product class). The page
  has to be selected on something that differs between them, usually a
  tag a rule writes.

Where extenders are managed devices, run the fleet check from
`home-links` as well:

```bash
python3 .claude/skills/home-links/check_home_links.py
```

Every failure it prints is a home whose gateway and extender disagree
about how they are connected, which no single page shows.

## 4. Whether the desk's tools work

The actions a device offers, and what happened when someone ran them:

```bash
curl -s "$HERDER_API/api/v1/devices/<id>/actions" -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.capabilities[] | [.capability, .profile] | @tsv'
curl -s "$HERDER_API/api/v1/devices/<id>/action-runs" -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.[] | [.capability, .status, .current_step, .current_step_name, (.collected_params // {} | keys | length)] | @tsv'
```

Aggregate by model, capability, status and failing step. Read a failure
by where it stopped and what it collected:

- **Failed at step 0 with nothing collected**: the device never ran it.
  Either the ACS could not reach it (step 5), or its firmware refused
  the first write. A profile offered on units that refuse it is a
  selector too wide for the hardware.
- **Failed at a later step with parameters collected**: the device ran
  it and the result was not what the profile waited for. A profile
  finding.
- **Every run of a capability failed on every model**: suspect the
  profile before the fleet.

## 5. Whether the ACS can reach the units

A unit behind the subscriber's NAT is reachable between informs only
over XMPP (TR-069 Annex K). Its account is written by a provisioning
rule, and nothing reports an error when it does not dial in.

- On a Herder that records them, the device's events carry
  `xmpp_connected` and `xmpp_disconnected`
  (`GET /api/v1/devices/<id>/events?kind=xmpp_connected`). A unit given
  an account with no `xmpp_connected` since has not dialled in.
- The cached `XMPP.Connection.{i}.Status` is only as fresh as its
  `updated_at`; a value last read days ago is no evidence either way.
- Walk the checklist in the CWMP guide
  (https://docs.herder.ispx.co/guides/cwmp/), and check the two things
  that live outside Herder: the XMPP domain has an A record and an
  `_xmpp-client._tcp` SRV record, and the port answers from outside:

  ```bash
  curl -s -H 'accept: application/dns-json' \
    "https://cloudflare-dns.com/dns-query?name=_xmpp-client._tcp.<domain>&type=SRV"
  ```

- More than one connection instance carrying the device's username
  means an earlier rule added a duplicate; the device may be pointed at
  the wrong one.

## 6. Firmware and advisories

```bash
curl -s "$HERDER_API/api/v1/firmware/images" -H "Authorization: Bearer $HERDER_TOKEN" \
| jq -r '.[] | [.version, (.device_selector|tostring), .description] | @tsv'
```

- **An image's selector must match the labels the device reports**, not
  the vendor's marketing name. A manufacturer label is what the CPE
  sends (a Nokia Beacon reports `ALCL`), so an image selected on
  `manufacturer: Nokia` matches some other Nokia product and none of
  the units it was uploaded for. Check each selector against step 1.
- **Advisories with placeholder content** (a title or remediation still
  reading as a template) flag every build they list with a finding
  nobody wrote. Delete them or fill them from the vendor's bulletin.

## What to hand back

A table per model: section or capability, verdict, evidence, and for
anything not working, where the fix belongs:

- **Shared content** (herder-public-configs): a vendor profile, a
  selector, a mapping, anything every operator with that hardware
  needs.
- **The operator's config repository**: their rules, their uploads
  moved into Git, their adopted recipes, their firmware and advisories.
- **Outside Herder**: DNS, firewall, the hosts. Name who owns it.
- **Herder itself**: a surface that cannot show what the operator needs
  to know. Report it to the maintainers with what you observed.

Name the Herder version and each model's firmware. Then rerun the
failing rows after the fixes land, because a fix nobody re-ran is a
claim.
