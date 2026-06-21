---
name: bundles-manage
destructive: true
description: Use when the user asks specifically about BUNDLES (a saved group of capabilities under one slash command): 'what bundles do I have', 'list my bundles', 'show the research bundle', or 'run a bundle'. Only for bundles; to view or manage an individual skill, use the skills manager instead.
---

# Skill bundles

A bundle groups several capabilities under one slash command (YAML in
~/.hermes/skill-bundles/). List and show bundles (read-only). To run a bundle,
invoke its slash command. Creating/deleting a bundle edits those YAML files;
deletion is destructive and is confirmed first.
