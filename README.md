# herder-workbench

The workbench for integrating a new CPE vendor into
[Herder](https://docs.herder.ispx.co/), built to be driven by a coding
agent. Clone it next to your fork of
[herder-public-configs](https://github.com/ispx-limited/herder-public-configs),
point an agent at it (the `CLAUDE.md` and the skills under
`.claude/skills/` are its instructions), and it walks the integration:
survey what the device exposes, read which platform features lack
bindings, write the `vendors/` directory, validate every buffer against
the live API, ship by Git.

Everything here also works without an agent. The skills are plain
markdown; each step is an API call or a file you can run and write
yourself.

## What you need

- A running Herder and an API token for it. The API base URL and token
  are read from `HERDER_API` and `HERDER_TOKEN` throughout, so export
  them before starting the agent:

  ```bash
  export HERDER_API=https://acs.example.net
  export HERDER_TOKEN=<your API token>
  ```

  An agent started without them asks for them.
- Your fork of herder-public-configs, wired as a config source.
- The CPE, connected. Real hardware, or a container: the
  [cpe-labs](https://github.com/ispx-limited/cpe-labs) fleet simulator
  models vendors you declare, and its icwmp harness runs a real TR-069
  client stack whose tree nobody modelled.

## What is in here

| Path | What it is |
|------|------------|
| `CLAUDE.md` | Agent context: vocabulary, the flow, the rules |
| `.claude/skills/onboard-vendor/` | The end-to-end integration workflow, with the feature menu the operator chooses from |
| `.claude/skills/survey-datamodel/` | Discover and summarize what a device exposes |
| `.claude/skills/mapping-gaps/` | Coverage gaps to mapping-table skeletons |
| `.claude/skills/wifi-experience/` | Per-client signal, the network map, the neighbour scan |
| `.claude/skills/firmware-readiness/` | Making a model safe to name in a firmware campaign |
| `.claude/skills/surface-audit/` | Every section of the device page checked across several units |
| `.claude/skills/migrate-genieacs/` | Porting GenieACS provisions, presets and virtual parameters to Herder rules, scripts and mappings |
| `.claude/agents/datamodel-surveyor.md` | A background agent that runs the survey while the session writes |

## What this is not

Not the ACS (that is Herder), not the configs (those live in your
herder-public-configs fork), not a CPE simulator (that is cpe-labs).
The workbench is the instructions layer that connects the three.

The guides the skills lean on are on the public docs site, starting
with [Vendor Onboarding](https://docs.herder.ispx.co/guides/vendor-onboarding/).
