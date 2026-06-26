# application

Deploys a **single** app from a source directory on the controller — its Podman
[Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
files, plain systemd units, and config tree — then enables and starts the units
you name. Invoke it once per app.

## Source layout

```
apps/
  <app>/
    quadlet/   # *.container, *.pod, *.network, *.volume, *.kube, ... (optional)
    unit/      # plain *.service, *.timer, *.socket, ...               (optional)
    config/    # arbitrary tree, copied recursively to the host        (optional)
```

Each subdirectory is optional — an app may ship only a Quadlet, only config, etc.

## What it does

1. Copies `<app>/quadlet/*` to `/etc/containers/systemd/` (the Quadlet generator dir).
2. Copies `<app>/unit/*` to `/etc/systemd/system/`.
3. Copies `<app>/config/` recursively to `/var/app/<app>/config/`.
4. Runs `systemctl daemon-reload` (once, only if anything changed).
5. Starts and enables each unit in `enable_units`.

## Role parameters

| Param          | Required | Purpose                                              |
| -------------- | -------- | ---------------------------------------------------- |
| `app`          | yes      | App directory name under `application_apps_dir`.     |
| `enable_units` | no       | systemd unit names to enable at boot and start now.  |

## Tunables (defaults)

| Variable                  | Default                      | Purpose                              |
| ------------------------- | ---------------------------- | ------------------------------------ |
| `application_apps_dir`    | `{{ playbook_dir }}/../apps` | Source of app definitions.           |
| `application_system_dir`  | `/etc/containers/systemd`    | Quadlet install dir on the host.     |
| `application_unit_dir`    | `/etc/systemd/system`        | Plain-unit install dir on the host.  |
| `application_config_root` | `/var/app`                   | Config root → `<root>/<app>/config`. |

## Unit names and boot persistence

`enable_units` takes the **generated** service name (the `.service` suffix is
optional):

| Quadlet file      | Generated unit         |
| ----------------- | ---------------------- |
| `foo.container`   | `foo.service`          |
| `foo.pod`         | `foo-pod.service`      |
| `foo.network`     | `foo-network.service`  |
| `foo.volume`      | `foo-volume.service`   |

Quadlet-generated services live under `/run` and cannot be `systemctl enable`d
directly. The role tolerates that specific failure and relies on an `[Install]`
section in the Quadlet (e.g. `WantedBy=multi-user.target`) for boot startup.
Plain units in `unit/` are enabled normally.

## Example

```yaml
- hosts: all
  become: true
  roles:
    - role: application
      app: calculators
      enable_units:
        - calculators.service
```
