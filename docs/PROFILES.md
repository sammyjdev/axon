# Prometheus Profiles

Profiles are named setup presets stored in `prometheus.toml`.

Today, a profile is intentionally small:

- a profile `name`
- a short `description`
- a runtime `mode`

Using a profile updates the active profile in `[runtime]`, syncs the runtime
`mode`, and, when `.env.local` exists next to `prometheus.toml`, also updates
`PROMETHEUS_RUNTIME_MODE`.

Profiles do not currently enable or disable individual subsystems. They are a
practical way to choose the operating shape that best fits your machine and
workflow.

## Built-in Profiles

Prometheus currently uses three built-in profile names:

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
3. Writes the chosen profile back into `prometheus.toml`.

This is a flag-driven recommender, not a full interactive wizard.
In practice, it selects among the built-in profiles above. If you want a named
`full-local` setup, create it explicitly with `pb profile create`.

### `pb profile list`

Shows the profiles defined in `prometheus.toml` and marks the active one.

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

### `pb profile use`

Switches to an existing profile and syncs runtime mode.

```bash
pb profile use team-dev
```

Use this when you already know which mode you want.

### `pb profile create`

Creates a new profile entry in `prometheus.toml`.

```bash
pb profile create support-lite \
  --description "Support workflow on lighter hardware" \
  --mode minimal
```

Supported modes are:

- `full-local`
- `hybrid-local`
- `remote-infra`
- `minimal`

This command only writes a profile entry. It does not activate it unless you
follow with `pb profile use`.

### `pb profile export`

Prints a profile as a TOML snippet.

```bash
pb profile export team-dev
```

Use it when you want to:

- copy a known-good profile into another machine's `prometheus.toml`
- share a profile in team docs or onboarding notes

## Worked Examples

### 1. Check the current setup and switch modes

Use this when Prometheus is already installed and you want to confirm the
active operating shape.

```bash
pb profile list
pb profile show
pb profile use privacy-first
pb profile show
```

Result: the active profile becomes `privacy-first`, the runtime mode becomes
`minimal`, and `.env.local` is updated if it exists.

### 2. Let Prometheus recommend a profile for a shared team setup

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
4. Add custom profiles with `pb profile create` only when the built-ins are not enough.

For broader environment guidance, see [Support matrix](SUPPORT_MATRIX.md).
