# AXON Profiles

Profiles are named setup presets stored in `axon.toml`.

Today, the CLI uses a deliberately small built-in profile model:

- a profile `name`
- a short `description`
- a runtime `mode`

Using a profile updates the active profile in `[runtime]`, syncs the runtime
`mode`, and, when `.env.local` exists next to `axon.toml`, also updates
`AXON_RUNTIME_MODE`.

Profiles do not currently enable or disable individual subsystems at runtime.
They are the current way to choose the operating shape that best fits your
machine and workflow. Optional structured profile fields exist in the config
schema, but they should be read as setup intent and future customization
metadata, not as a finished feature-flag system.

## Built-in Profiles

AXON currently uses three built-in profile names:

| Profile | Mode | Use it when |
| --- | --- | --- |
| `solo-dev` | `hybrid-local` | You are working alone and want the engine on your machine without forcing the heaviest local setup. This is the default fit for a single developer on macOS or a general-purpose laptop. |
| `team-dev` | `remote-infra` | You want the CLI local but expect heavier shared services or model infrastructure to run elsewhere. This is the best fit for shared team setups and remote service hosts. |
| `privacy-first` | `minimal` | You want the smallest supported setup or you are handling more sensitive workflows and prefer the lightest, safest operating mode. This is the usual recommendation for lower-resource machines and restricted data. |

The current built-in mapping is simple and explicit:

- `solo-dev` means `hybrid-local`
- `team-dev` means `remote-infra`
- `privacy-first` means `minimal`

If you need `full-local`, create a custom profile for it.

## Profile Manifest

AXON can store more than the three core fields above in
`axon.toml`.

Supported profile keys today:

| Key | Meaning | Current behavior |
| --- | --- | --- |
| `description` | short human label | shown in profile listings and export |
| `mode` | operating shape: `full-local`, `hybrid-local`, `remote-infra`, `minimal` | applied by `pb profile use` and `pb configure` |
| `cloud_policy` | whether cloud assistance is acceptable | stored, exported, shown in `pb profile show`, and used by capability selection |
| `infra_strategy` | whether heavier services should be local or remote | stored, exported, shown in `pb profile show`, and used by capability selection |
| `memory_tier` | lighter vs fuller memory footprint | stored, exported, shown in `pb profile show`, and used by capability selection |
| `enabled_features` | named capabilities you intend to keep visible | stored, exported, shown in `pb profile show`, and folded into capability selection |

Example:

```toml
[profiles.support-lite]
description = "Support workflow on lighter hardware"
mode = "minimal"
cloud_policy = "deny"
infra_strategy = "local"
memory_tier = "light"
enabled_features = ["rtk", "local-rag"]
```

This richer shape matters for P1 because it lets AXON represent user
choices in language that is closer to actual setup decisions, even though the
current CLI still applies only the profile name and `mode`.

## How Custom Fields Map To Real Setup Choices

These optional fields are intended to capture the decisions a user is really
making:

| Field | User choice it represents | Typical interpretation |
| --- | --- | --- |
| `cloud_policy = "deny"` | "Do not depend on hosted model calls for this setup." | prefer the smallest local or self-hosted path |
| `cloud_policy = "avoid"` | "Use cloud only if local paths are not enough." | keep hybrid paths available but not primary |
| `infra_strategy = "local"` | "Run the important services on this machine." | fits `full-local`, `hybrid-local`, or `minimal` depending on hardware |
| `infra_strategy = "remote"` | "Keep the CLI local but move heavy infra elsewhere." | usually points toward `remote-infra` |
| `memory_tier = "light"` | "I want lower resource cost and less operational weight." | usually aligns with `minimal` |
| `memory_tier = "full"` | "I can afford the heavier memory-related pieces." | usually aligns with fuller local or shared setups |
| `enabled_features = ["rtk", ...]` | "These are the capabilities I actively want surfaced." | a future capability selector can use this to hide overkill |

Important boundary: the current implementation now interprets those fields for
capability selection and profile inspection, but it still does not start or
stop services, rewrite compose files, or toggle retrieval subsystems directly.

## How Profiles Fit The CLI

### `pb configure`

`pb configure` recommends and applies a profile based on a few product-facing
inputs:

```bash
pb configure \
  --use-case solo|team|corporate \
  --privacy public|internal|confidential|restricted \
  --hardware cpu-only|mac-laptop|nvidia|linux-workstation
```

Optional overrides:

- `--preferred-mode full-local|hybrid-local|remote-infra|minimal`
- `--cloud ok|avoid|deny`
- `--infra local|remote`
- `--memory light|full`

What it does today:

1. Recommends a profile and mode.
2. Applies that profile immediately.
3. Writes the chosen profile back into `axon.toml`.

When any of the core fields are omitted, `pb configure` falls back to a concise
interactive flow using the same logic in question form:

1. Ask what kind of work you are doing: solo, team, or more restricted/corporate.
2. Ask about privacy sensitivity and whether cloud use is acceptable.
3. Ask what hardware and infrastructure you actually have available.
4. Recommend a mode and profile that remove obvious overkill.
5. Let you keep the recommendation or refine it into a named custom profile.

The explicit flag path still works, which keeps the behavior deterministic and
scriptable for automation.

### Capability Selection And Overkill Hiding

P1 introduces the idea that AXON should hide advanced pieces when they do
not solve the user's immediate problem. The current implementation expresses
that through a capability selector fed by profile metadata and recommendation
inputs.

The practical mapping today is:

| If the user signals... | `pb configure` tends to recommend... | Why |
| --- | --- | --- |
| restricted or confidential data | `privacy-first` + `minimal` | smallest supported shape, least operational sprawl |
| a shared or corporate setup | `team-dev` + `remote-infra` | move heavier infra off the laptop |
| light memory preference | `privacy-first` + `minimal` | lower footprint beats extra capability |
| explicit remote infra | `team-dev` + `remote-infra` | keep CLI local, outsource the heavy services |
| a typical solo setup | `solo-dev` + `hybrid-local` | enough local capability without assuming the heaviest stack |

What is not implemented yet:

- automatic hiding or disabling of specific subsystems from `enabled_features`
- service provisioning changes driven directly from profile metadata
- a richer custom profile wizard beyond the current prompt-and-flags flow

### `pb profile list`

Shows the profiles defined in `axon.toml` and marks the active one.

```bash
pb profile list
```

Expected shape:

```text
* solo-dev | mode=hybrid-local | Single developer default
- team-dev | mode=remote-infra | Shared team setup
- privacy-first | mode=minimal | Prefer local or remote self-hosted paths
```

### `pb profile show`

Shows the active profile in detail.

```bash
pb profile show
```

Today this prints the core fields, optional structured metadata, and the
selected/overkill capabilities inferred from that profile.

### `pb profile use`

Switches to an existing profile and syncs runtime mode.

```bash
pb profile use team-dev
```

Use this when you already know which mode you want.

### `pb profile create`

Creates a new profile entry in `axon.toml`.

```bash
pb profile create support-lite \
  --description "Support workflow on lighter hardware" \
  --mode minimal \
  --cloud-policy deny \
  --infra-strategy local \
  --memory-tier light \
  --enabled-features rtk,local-rag
```

Supported modes are:

- `full-local`
- `hybrid-local`
- `remote-infra`
- `minimal`

This command only writes a profile entry. It does not activate it unless you
follow with `pb profile use`.

The CLI variant of `create` can now write all supported structured fields. You
can still edit `axon.toml` directly when you want more control, then use
`pb profile export` to verify the resulting shape.

### `pb profile export`

Prints a profile as a TOML snippet.

```bash
pb profile export team-dev
```

Use it when you want to:

- copy a known-good profile into another machine's `axon.toml`
- share a profile in team docs or onboarding notes

## Worked Examples

### 1. Check the current setup and switch modes

Use this when AXON is already installed and you want to confirm the
active operating shape.

```bash
pb profile list
pb profile show
pb profile use privacy-first
pb profile show
```

Result: the active profile becomes `privacy-first`, the runtime mode becomes
`minimal`, and `.env.local` is updated if it exists.

### 2. Let AXON recommend a profile for a shared team setup

```bash
pb configure \
  --use-case team \
  --privacy internal \
  --hardware nvidia
```

Current outcome:

- recommended profile: `team-dev`
- recommended mode: `remote-infra`

This is the right path when the CLI stays local but the heavier services are
better hosted on a shared machine.

### 2b. The same setup through the interactive flow

This is the decision sequence the guided `pb configure` flow now represents:

1. "Is this mainly solo or team work?" -> team
2. "How sensitive is the data?" -> internal
3. "What hardware or infra do you have?" -> heavier services can live remotely
4. Recommendation -> `team-dev`
5. Applied mode -> `remote-infra`

That framing is useful because it explains why the resulting profile is a good
fit, not just what the CLI selected.

### 3. Create a custom full-local profile for a stronger Linux box

```bash
pb profile create workstation \
  --description "Single-machine workstation setup" \
  --mode full-local

pb profile use workstation
pb profile export workstation
```

Use this when the built-in profiles are close but not exact and you want a
named preset for a machine that can comfortably run the full stack locally.

## Recommended Flow

For most users, the shortest path is:

1. Run `pb configure` to get an initial recommendation.
2. Verify with `pb profile show`.
3. Switch later with `pb profile use` if your setup changes.
4. Add a custom profile with `pb profile create` when the built-ins are not enough.
5. Add optional profile metadata through `pb profile create` or directly in `axon.toml` when you need a custom setup shape.

For broader environment guidance, see [Support matrix](SUPPORT_MATRIX.md).
