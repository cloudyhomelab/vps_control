# application

Deploys (or decommissions) a **single** app behind the reverse proxy, plus an
optional Caddy route. Invoke it once per app. Two required selectors drive it:

- **`application_kind`** — `source` or `simple` (see below). **Required, no default.**
- **`application_state`** — `present` (default) deploys; `absent` decommissions.

## Kinds

### `source` — install from a directory

The app ships a directory on the controller; the role copies its Podman
[Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
files, plain systemd units, and config tree to the host.

```
apps/
  <app>/
    quadlet/   # *.container, *.pod, *.network, *.volume, *.kube, ... (optional)
    unit/      # plain *.service, *.timer, *.socket, ...               (optional)
    config/    # arbitrary tree, copied recursively to the host        (optional)
```

Each subdirectory is optional — an app may ship only a Quadlet, only config, etc.

### `simple` — render a single container

For the common "one image behind the reverse proxy" case, the role renders a
single `<name>.container` Quadlet from inline parameters — no source directory
needed. Just give it an image (and usually a domain). The container is named
`<application_name>`, joins `application_network`, and is auto-updated
(`AutoUpdate=registry`).

## What it does

### `present` (default)

1. **`simple`**: renders `<name>.container` to `/etc/containers/systemd/`.
   **`source`**: copies `<app>/quadlet/*` there and `<app>/unit/*` to
   `/etc/systemd/system/`, then copies `<app>/config/` to `/var/app/<app>/config/`.
   The installed host paths are recorded in an [install manifest](#install-manifest);
   anything the previous deploy recorded and this one no longer installs (a renamed
   or deleted file) is removed from the host.
2. When `application_domain` is set, writes a Caddy route snippet to the imported
   `conf.d/` directory (see [Caddy routing](#caddy-routing)).
3. Runs `systemctl daemon-reload` (once, only if anything changed).
4. Starts and enables the [managed units](#unit-names-and-boot-persistence).

### `absent`

1. Stops the managed units (a Quadlet service's `ExecStopPost` also removes its
   container).
2. Removes the app's Quadlet from the host — the rendered `<name>.container`
   (`simple`) or exactly the paths in the app's [install manifest](#install-manifest)
   (`source`) — and the `<name>.caddy` route snippet.
3. Runs `systemctl daemon-reload`.

Config/data under `/var/app/<app>` is **left in place** — it may hold persistent
state, and `reverse_proxy`'s tree holds other apps' route snippets. The route
stops resolving when the deploy playbook reloads Caddy (post-task). After a
decommission has run on the host, delete the role call.

## Role parameters

| Param                      | Required        | Purpose                                                       |
| -------------------------- | --------------- | ------------------------------------------------------------- |
| `application_kind`         | yes             | `source` (files from a dir) or `simple` (rendered container). |
| `application_name`         | yes             | `source`: app dir name. `simple`: container name / DNS name.  |
| `application_state`        | no              | `present` (default) deploys; `absent` decommissions.          |
| `application_enable_units` | no              | systemd unit names to enable and start (see below).           |
| `application_domain`       | no              | Public hostname; when set, a Caddy route is added.            |
| `application_upstream`     | no              | Upstream container name to proxy to (default `application_name`). |
| `application_port`         | no              | Upstream port for the Caddy route (default `8080`).           |

### `simple`-kind parameters

| Param                       | Required             | Purpose                                            |
| --------------------------- | -------------------- | -------------------------------------------------- |
| `application_image`         | yes (simple+present) | Full image ref, e.g. `docker.io/org/app:latest`.   |
| `application_description`   | no                   | Unit description (default `<name> container`).     |
| `application_network`       | no                   | Network the container joins (default `web.network`). |
| `application_env`           | no                   | Env vars rendered as `Environment=` lines.         |
| `application_volumes`       | no                   | Raw `Volume=` values.                              |
| `application_publish_ports` | no                   | Raw `PublishPort=` values.                         |
| `application_container_options` | no               | Raw lines for the `[Container]` section.           |
| `application_service_options` | no                 | Raw lines for the `[Service]` section.            |
| `application_health_cmd`    | no                   | Probe command; enables the health block (see below). |

## Tunables (defaults)

| Variable                  | Default                      | Purpose                                  |
| ------------------------- | ---------------------------- | ---------------------------------------- |
| `application_apps_dir`    | `{{ playbook_dir }}/../apps` | Source of `source`-kind app definitions. |
| `application_system_dir`  | `/etc/containers/systemd`    | Quadlet install dir on the host.         |
| `application_unit_dir`    | `/etc/systemd/system`        | Plain-unit install dir on the host.      |
| `application_config_root` | `/var/app`                   | Config root → `<root>/<app>/config`.     |
| `application_manifest_dir` | `/var/lib/application`      | Install manifests, one per `source` app. |
| `application_caddy_confd` | `/var/app/reverse_proxy/config/conf.d` | Dir for generated route snippets. |

## Install manifest

A `source` deploy records the absolute host paths it installed to
`/var/lib/application/<app>.manifest`, one per line. Both a later deploy
and a decommission work from that file rather than re-globbing
`apps/<app>/{quadlet,unit}/`, which by then may name different files or be gone
entirely — so a renamed or deleted Quadlet does not linger on the host as an
orphaned unit, and a decommission needs no source tree at all.

The manifest is host state acted on as root, so every recorded path is checked
against `application_system_dir` / `application_unit_dir` before anything is
removed; a manifest listing a path outside them fails the run without deleting
anything.

Only files are reconciled. A unit dropped from the app that is still running keeps
running until it is stopped or the host reboots — remove it from
`application_enable_units` and stop it once by hand.

## Caddy routing

Set `application_domain` to expose the app through the reverse proxy without
editing the central `Caddyfile`. The role drops a `<application_name>.caddy`
snippet (`domain → upstream:port`) into `application_caddy_confd`, which the
Caddyfile imports via `import /etc/caddy/conf.d/*.caddy`. The deploy playbook
reloads Caddy once as a post-task, so the route applies after all apps converge.

`application_upstream` defaults to `application_name` — correct for every `simple`
app (the container *is* `<name>`) and for `source` apps whose `ContainerName=`
matches the app name. Override it when a `source` app's routable container is
named differently. On `absent`, the role removes the `<name>.caddy` snippet it
generated.

## Healthchecks and auto-update rollback

`podman auto-update` reverts an image only when the unit **fails to start**. Without a
healthcheck a container that boots and then serves errors counts as started, so the bad
image stays. `Notify=healthy` closes that gap: systemd withholds "started" until the
first probe passes, which turns a broken image into a failed start that gets rolled back.

For a `simple` app, set `application_health_cmd` and the role emits the whole block —
`HealthCmd`, `HealthInterval`, `HealthRetries`, `HealthStartPeriod`, `Notify=healthy`,
and a matching `TimeoutStartSec`. Only the command is per-app, because nothing else can
be: an HTTP app wants a request, Postgres wants `pg_isready`. Everything around it is
fleet policy in `defaults/main.yml` and rarely needs overriding.

```yaml
    - role: application
      application_kind: simple
      application_name: nasplan
      application_health_cmd: "wget -qO /dev/null http://127.0.0.1:8080/ || exit 1"
```

Two things to know. The command runs **inside** the container, so the tool has to exist
in the image — check with `podman exec <name> sh -lc 'command -v wget curl'` before
relying on it, because a probe that can never pass makes the deploy itself fail. And
address the app as `127.0.0.1`, not `localhost`, which resolves to `::1` on musl images
where nothing is listening. Leave `application_health_cmd` empty for an app that cannot
be probed; it then gets no health block and no rollback protection.

`source` apps declare these keys in their own Quadlet files (see
`apps/calculators/quadlet/calculators.container`), so this policy does not reach them.

## Unit names and boot persistence

The role starts/enables its **managed units**: `application_enable_units` if you
list any, otherwise — for a `simple` app — the single generated `<name>.service`.
A `source` app that lists none starts nothing and relies entirely on its
`[Install]` section (e.g. the network-only `shared` app).

`application_enable_units` takes the **generated** service name (the `.service`
suffix is optional):

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

## Examples

A `source` app with a Caddy route:

```yaml
- hosts: all
  become: true
  roles:
    - role: application
      application_kind: source
      application_name: calculators
      application_enable_units:
        - calculators.service
      # Optional: route calc.cloudyhome.org -> calculators:8080 via Caddy.
      application_domain: calc.cloudyhome.org
```

A `simple` app — one image behind the reverse proxy, no source dir:

```yaml
    - role: application
      application_kind: simple
      application_name: nasplan
      application_image: docker.io/binarycodes/make-my-nas:latest
      application_port: 8080
      application_domain: nasplan.cloudyhome.org
```

Decommission an app (leave the call in place for one converge, then delete it):

```yaml
    - role: application
      application_state: absent
      application_kind: simple
      application_name: whoami
```
