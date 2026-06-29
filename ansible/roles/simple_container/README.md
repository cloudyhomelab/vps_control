# simple_container

Deploys a **single-image web app behind the reverse proxy** from a minimal spec.
In the common case you give it just `simple_container_image` (and a
`simple_container_domain` if it should be reachable from the internet) — the role
renders the Podman
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
      simple_container_name: whoami
      simple_container_image: docker.io/traefik/whoami:latest
      simple_container_domain: whoami.cloudyhome.org   # omit for internal-only
      simple_container_port: 80                         # upstream port, default 8080
```

That single block is equivalent to writing a full `whoami.container` Quadlet
**and** adding a `reverse_proxy whoami:80` site to the Caddyfile by hand.

## What it does

1. Renders `<name>.container` to `/etc/containers/systemd/` from a template —
   filling in `ContainerName`, `Network=web.network`, `AutoUpdate=registry`,
   `Restart=always`, `network-online.target` ordering, and the `[Install]`
   section automatically.
2. When `simple_container_domain` is set, writes `<name>.caddy` (a
   `reverse_proxy name:port` site) into the Caddy `conf.d` directory and reloads
   Caddy.
3. Runs `systemctl daemon-reload`, then starts and enables `<name>.service`.

## Role parameters

All parameters are role-prefixed (`var-naming[no-role-prefix]`). The prefix also
keeps them clear of Ansible footguns: a bare `name` is swallowed as the reserved
`roles:` entry label, and a bare `port` is read as the SSH connection port.

| Param                            | Required | Purpose                                                        |
| -------------------------------- | -------- | -------------------------------------------------------------- |
| `simple_container_state`         | no       | `present` (default) deploys; `absent` decommissions.           |
| `simple_container_name`          | yes      | Container name; Quadlet basename and network DNS name.         |
| `simple_container_image`         | when present | Full image reference, e.g. `docker.io/org/app:latest`.      |
| `simple_container_domain`        | no       | Public hostname; when set, registers a Caddy route.            |
| `simple_container_port`          | no       | Internal upstream port for the route (default `8080`).         |
| `simple_container_description`   | no       | Unit description (default `"<name> container"`).               |
| `simple_container_network`       | no       | Podman network to join (default `web.network`).                |
| `simple_container_env`           | no       | Dict rendered as `Environment=` lines.                         |
| `simple_container_volumes`       | no       | List of raw `Volume=` values.                                  |
| `simple_container_publish_ports` | no       | List of raw `PublishPort=` values (apps that need a host port).|
| `simple_container_extra_options` | no       | Extra raw lines appended to the `[Container]` section.         |

> `simple_container_port` is the **internal** upstream port used only for the
> Caddy route — it is not published to the host. Use
> `simple_container_publish_ports` for apps that must bind a host port directly.

## Decommissioning an app

The playbook converges declared state but does not garbage-collect apps you
simply delete from it — a removed role call just stops being managed, leaving its
container, Quadlet, and route running on the host. To remove an app cleanly,
**flip it to `absent`** rather than deleting the call:

```yaml
- role: simple_container
  simple_container_state: absent
  simple_container_name: whoami
```

On the next deploy the role stops the service (Quadlet removes the container),
deletes the `<name>.container` Quadlet and the `<name>.caddy` route, and
daemon-reloads; the playbook's post-task `caddy reload` then drops the route.
Once it has run successfully you can delete the role call entirely. (Container
images are left in place — remove them with `podman image prune` if desired.)

## Tunables (defaults)

| Variable                       | Default                                | Purpose                          |
| ------------------------------ | -------------------------------------- | -------------------------------- |
| `simple_container_system_dir`  | `/etc/containers/systemd`              | Quadlet install dir on the host. |
| `simple_container_network`     | `web.network`                          | Default network to join.         |
| `simple_container_port`        | `8080`                                 | Default internal upstream port.  |
| `simple_container_caddy_confd` | `/var/app/reverse_proxy/config/conf.d` | Where route snippets are written.|

## How Caddy routing stays per-app

The reverse proxy's `Caddyfile` ends with `import /etc/caddy/conf.d/*.caddy`,
and the `reverse_proxy` app mounts the host directory
`/var/app/reverse_proxy/config/conf.d` read-only at `/etc/caddy/conf.d`. Each
`simple_container` invocation drops its own `<name>.caddy` snippet there, so apps
never share or hand-edit a central file.

Caddy reads the imported files at load time, so the **deploy playbook** issues a
single graceful `systemctl reload caddy` as a post-task after all apps are in
place (wired to the unit's `ExecReload`, which calls `caddy reload` over the
admin API). This is zero-downtime and reuses existing certs — no ACME
re-issuance. The role itself does **not** reload Caddy; routing is refreshed
centrally so a recreated upstream is always picked up, even when its route text
is unchanged.

Deploy `reverse_proxy` **before** any `simple_container` app so `caddy.service`
exists when the post-task reload runs.
