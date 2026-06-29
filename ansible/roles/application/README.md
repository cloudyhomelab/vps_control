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
2. When `application_domain` is set, writes a Caddy route snippet to the imported
   `conf.d/` directory (see [Caddy routing](#caddy-routing)).
3. Runs `systemctl daemon-reload` (once, only if anything changed).
4. Starts and enables the [managed units](#unit-names-and-boot-persistence).

### `absent`

1. Stops the managed units (a Quadlet service's `ExecStopPost` also removes its
   container).
2. Removes the app's Quadlet from the host — the rendered `<name>.container`
   (`simple`) or the installed `<app>/quadlet/*` and `<app>/unit/*` files,
   matched by source basename (`source`) — and the `<name>.caddy` route snippet.
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
| `application_extra_options` | no                   | Extra raw lines for the `[Container]` section.     |

## Tunables (defaults)

| Variable                  | Default                      | Purpose                                  |
| ------------------------- | ---------------------------- | ---------------------------------------- |
| `application_apps_dir`    | `{{ playbook_dir }}/../apps` | Source of `source`-kind app definitions. |
| `application_system_dir`  | `/etc/containers/systemd`    | Quadlet install dir on the host.         |
| `application_unit_dir`    | `/etc/systemd/system`        | Plain-unit install dir on the host.      |
| `application_config_root` | `/var/app`                   | Config root → `<root>/<app>/config`.     |
| `application_caddy_confd` | `/var/app/reverse_proxy/config/conf.d` | Dir for generated route snippets. |

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
