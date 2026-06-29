# simple_container

Deploys a **single-image web app behind the reverse proxy** from a minimal spec.
In the common case you give it just an `image` (and a `domain` if it should be
reachable from the internet) — the role renders the Podman
[Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
for you, joins it to the shared `web.network`, and registers its Caddy route.

Use this instead of hand-writing a `<app>.container` and editing the `Caddyfile`
for the "internal app, one image, behind Caddy" pattern. For anything richer —
multiple Quadlet files, volumes/networks of its own, plain systemd units, or an
arbitrary config tree — use the [`application`](../application/README.md) role.

## Example

```yaml
- hosts: all
  become: true
  roles:
    - role: simple_container
      name: whoami
      image: docker.io/traefik/whoami:latest
      domain: whoami.cloudyhome.org   # omit for an internal-only app
      port: 80                        # internal upstream port, default 8080
```

That single block is equivalent to writing a full `whoami.container` Quadlet
**and** adding a `reverse_proxy whoami:80` site to the Caddyfile by hand.

## What it does

1. Renders `<name>.container` to `/etc/containers/systemd/` from a template —
   filling in `ContainerName`, `Network=web.network`, `AutoUpdate=registry`,
   `Restart=always`, `network-online.target` ordering, and the `[Install]`
   section automatically.
2. When `domain` is set, writes `<name>.caddy` (a `reverse_proxy name:port`
   site) into the Caddy `conf.d` directory and reloads Caddy.
3. Runs `systemctl daemon-reload`, then starts and enables `<name>.service`.

## Role parameters

| Param           | Required | Purpose                                                        |
| --------------- | -------- | -------------------------------------------------------------- |
| `name`          | yes      | Container name; Quadlet basename and network DNS name.         |
| `image`         | yes      | Full image reference, e.g. `docker.io/org/app:latest`.         |
| `domain`        | no       | Public hostname; when set, registers a Caddy route.            |
| `port`          | no       | Internal upstream port for the route (default `8080`).         |
| `description`   | no       | Unit description (default `"<name> container"`).               |
| `network`       | no       | Podman network to join (default `web.network`).                |
| `env`           | no       | Dict rendered as `Environment=` lines.                         |
| `volumes`       | no       | List of raw `Volume=` values.                                  |
| `publish_ports` | no       | List of raw `PublishPort=` values (apps that need a host port).|
| `extra_options` | no       | Extra raw lines appended to the `[Container]` section.         |

> `port` is the **internal** upstream port used only for the Caddy route — it is
> not published to the host. Use `publish_ports` for apps that must bind a host
> port directly.

## Tunables (defaults)

| Variable                       | Default                              | Purpose                          |
| ------------------------------ | ------------------------------------ | -------------------------------- |
| `simple_container_system_dir`  | `/etc/containers/systemd`            | Quadlet install dir on the host. |
| `simple_container_network`     | `web.network`                        | Default network to join.         |
| `simple_container_port`        | `8080`                               | Default internal upstream port.  |
| `simple_container_caddy_confd` | `/var/app/reverse_proxy/config/conf.d` | Where route snippets are written.|
| `simple_container_caddy_unit`  | `caddy.service`                      | Unit reloaded after a route change.|

## How Caddy routing stays per-app

The reverse proxy's `Caddyfile` ends with `import /etc/caddy/conf.d/*.caddy`,
and the `reverse_proxy` app mounts the host directory
`/var/app/reverse_proxy/config/conf.d` read-only at `/etc/caddy/conf.d`. Each
`simple_container` invocation drops its own `<name>.caddy` snippet there, so apps
never share or hand-edit a central file. Because Caddy reads the imported files
at startup, the role restarts `caddy.service` when a snippet changes.

This means the reverse proxy must already be deployed (so `caddy.service` exists)
when a `simple_container` app with a `domain` runs — deploy it **after**
`reverse_proxy` in the playbook.
