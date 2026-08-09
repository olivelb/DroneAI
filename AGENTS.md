# DroneAI repository instructions

- Run every GitHub operation for this repository through the authenticated
  `gh` CLI in the authoritative Ubuntu WSL checkout. This includes pull-request
  creation and updates, checks and logs, reviews, auto-merge, merge, and remote
  repository metadata.
- Do not use the GitHub connector or GitHub MCP mutation tools for this
  repository. They do not have the required pull-request write permission.
- Keep required branch protections and CI gates enabled; do not bypass them to
  compensate for missing connector permissions.
