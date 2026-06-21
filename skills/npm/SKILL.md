---
name: npm
description: "npm registry ops: login, whoami, names, publish; 1Password tmux."
metadata: {"clawdbot":{"emoji":"📦","requires":{"bins":["npm","node","tmux","op","jq"]}}}
---

# npm

Use for npm registry/account tasks: `npm whoami`, package availability, package reservation, publish, org checks, and auth debugging.

## Auth

- Use `one-password` first for secret rules.
- Never run `op` directly in the shell tool.
- Known npm 1Password item: `npmjs` on `my.1password.com`.
- The item may contain username/password/TOTP, not a stored npm token. That is fine.
- Explicit user requests to `release`, `publish`, or `npm publish` are consent to complete npm auth, including a desktop 1Password sign-in/unlock prompt for the known `npmjs` item when service-account access cannot read it. Do not stop to ask for separate permission just because the npm auth prompt is expected.
- Still stop and ask if the `npmjs` item is missing, the account/vault is ambiguous, credentials are malformed, npm denies package access, or the requested package/version does not match the repo release target.
- Run npm auth work inside one persistent tmux session. Reuse it on failure.
- Keep npm auth in a temp npmrc; delete it after the command.
- If hand-rolling, read `npmjs` once, keep secrets in shell variables, require a six-digit `op item get npmjs --account my.1password.com --otp`, write a temp npmrc, run all npm commands with `NPM_CONFIG_USERCONFIG`, then delete the npmrc and unset variables.
- npm 11 prompt piping is brittle; avoid `printf ... | npm login --auth-type=legacy`.
- Avoid `expect` for npm login unless necessary; logs can echo prompts and are easy to get wrong.
- Prefer the helper's registry API login path (`npm-profile` `loginCouch`) for automation.
- If auth shape is ambiguous or `npm whoami` fails, stop and ask for the exact field label / credential fix. Do not probe more 1Password items or start another tmux session.

## Package Reservation

Use `scripts/reserve-packages.sh` from inside the same tmux session:

```bash
/Users/steipete/Projects/agent-scripts/skills/npm/scripts/reserve-packages.sh package-one package-two
```

What it does:
- reads `npmjs` once via `op`
- creates an npm registry session from username/password/TOTP
- publishes `0.0.0` placeholder packages with a generic README
- continues after per-package publish failures
- redacts tokens/OTP in logs
- cleans temp npmrc/work dirs

Notes:
- npm may reject names as too similar to already-published names. Treat that as a registry policy result, not an auth failure.
- npm CLI prompt piping is brittle on npm 11. Prefer the helper’s registry API login path over scripted `npm login`.
- For scoped packages, `npm view` can lag/404 even when the package exists. Check `npm access get status <pkg>`; `public` or a publish failure saying `previously published versions` means the name is reserved.
