# `systemd_app` molecule scenario

Converges the `systemd_app` role against a throwaway container that runs systemd as PID 1
with podman inside it, then asserts the host state it produced. The role's set arithmetic
and input checks are covered by `ansible/tests/` as Python; this covers what only a real
host shows — the install manifest, the prune it drives, a change of kind, and
decommissioning.

## What it exercises

| Stage | Covers |
| --- | --- |
| `converge.yml` | both kinds side by side: a `source` app shipping a Quadlet, a plain unit and a nested config tree, and an `inline` app rendered from call-site parameters |
| `idempotence` | a second converge changes nothing |
| `verify.yml` | installed paths, the recorded manifest of each kind, the rendered Quadlet (including `systemd_env_lines`' quoting), both route snippets, the pre-created data directory's ownership, and unit state |
| `side_effect.yml` | a file dropped from the app's source tree is pruned and unrecorded; the same app converted `source` → `inline` prunes what the other kind installed; `absent` removes everything the apps owned and stays green when repeated |

## Running it

```sh
pipx inject ansible-core molecule 'molecule-plugins[podman]' --include-apps
cd ansible/extensions
sudo env "PATH=$PATH" molecule test
```

`sudo`, because the outer container is privileged and runs a rootful podman of its own:
rootful inside rootful is the nesting that needs the fewest concessions — under a rootless
outer podman the inner one also wants `/dev/fuse` and subuid ranges of its own.
`molecule converge` leaves the container up for `podman exec -it systemd-app bash`;
`molecule destroy` removes it.

The scenario deliberately does not run the fixture containers on a podman network
(`Network=none`, `systemd_app_network: none`): joining one would put netavark in the
critical path of every run without covering anything the role does. Its fixture apps are
copied out of `apps/` into molecule's ephemeral directory first, so `side_effect.yml` can
drop a file from an app's source tree without editing a versioned file.
