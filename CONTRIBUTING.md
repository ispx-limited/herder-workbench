# Contributing

Wanted: corrections where a skill's instructions have drifted from the
Herder API, and sharpening that makes a step less ambiguous for an
agent. New skills are welcome when they cover a real integration task
the existing three do not.

There is nothing to build. Test a change by running the skill it
touches against a live Herder: every API call in these files is meant
to work as written, with `HERDER_API` and `HERDER_TOKEN` set. A skill
step that does not work as written is a defect.

Commits follow Conventional Commits (`docs(skills): ...`). One concern
per PR. Prose follows the house style: plain words, short sentences,
no em dashes, no emoji.
