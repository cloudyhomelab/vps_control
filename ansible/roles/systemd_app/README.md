# systemd_app

Deploys (or decommissions) a **single** app behind the reverse proxy, plus an
optional Caddy route. Invoke it once per app. Two required selectors drive it:

- **`systemd_app_kind`** — `source` or `simple` (see below). **Required, no default.**
- **`systemd_app_state`** — `present` (default) deploys; `absent` decommissions.

## Kinds

### `source` — install from a directory

The app ships a directory on the controller; the role copies its Podman
[Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
files, plain systemd units, and config tree to the host.

```
apps/
  <app>/
    quadlet/            # *.container, *.pod, *.network, *.volume, *.kube, ... (optional)
    unit/               # plain *.service, *.timer, *.socket, ...              (optional)
    config/             # arbitrary tree, copied recursively to the host       (optional)
    secrets.sops.yaml   # SOPS-encrypted podman secrets                        (optional)
```

A `simple` app has no such directory, except when it needs secrets: then it holds that one
file and nothing else.

Each subdirectory is optional — an app may ship only a Quadlet, only config, etc.

### `simple` — render a single container

For the common "one image behind the reverse proxy" case, the role renders a
single `<name>.container` Quadlet from inline parameters — no source directory
needed. Just give it an image (and usually a domain). The container is named
`<systemd_app_name>`, joins `systemd_app_network`, and is auto-updated
(`AutoUpdate=registry`).

## What it does

### `present` (default)

1. **`simple`**: renders `<name>.container` to `/etc/containers/systemd/`.
   **`source`**: copies `<app>/quadlet/*` there and `<app>/unit/*` to
   `/etc/systemd/system/`, then copies `<app>/config/` to `/var/app/<app>/config/`.
   Either way, every host path installed is recorded in an
   [install manifest](#install-manifest); anything the previous deploy recorded and
   this one no longer installs (a renamed or deleted file, in `config/` as much as in
   `quadlet/`, or a file belonging to the kind the app has just stopped being) is
   removed from the host.
2. When `systemd_app_domain` is set, writes a Caddy route snippet to the imported
   `conf.d/` directory (see [Caddy routing](#caddy-routing)).
3. Runs `systemctl daemon-reload` (once, only if anything changed).
4. Starts and enables the [managed units](#unit-names-and-boot-persistence), then makes
   whatever this deploy changed take effect: a **restart** for a changed unit definition
   or secret, a **reload** for a changed config tree (see
   [Making changes take effect](#making-changes-take-effect)).

### `absent`

1. Stops the managed units (a Quadlet service's `ExecStopPost` also removes its
   container).
2. Removes exactly the paths in the app's [install manifest](#install-manifest) — its
   Quadlet files, plain systemd units and config tree — and the `<name>.caddy` route
   snippet.
3. Removes `/var/app/<app>` entirely: its deployed config and any data a Quadlet
   bind-mounts under it.
4. Removes the app's podman secrets, by the names recorded in
   `/var/app/<app>/.secret-digests` — read before that directory goes.
5. Runs `systemctl daemon-reload`.

> **`absent` is destructive and not reversible. Back the app up before setting it.**
> Whatever is under `/var/app/<app>` goes, including anything the container wrote
> there. The route stops resolving when the deploy playbook reloads Caddy (post-task).
> After a decommission has run on the host, delete the role call.

Two things `absent` does **not** remove, because they outlive the units that declared
them and one of them holds Caddy's TLS certificates: **Podman named volumes**
(`caddy-data`, `caddy-config`) and **networks** (`web`). Removing the `.volume`/`.network`
Quadlet file does not delete the volume or network it created. Clear those by hand with
`podman volume rm` / `podman network rm` once you are sure — and note that discarding
`caddy-data` forces certificate re-issuance, which Let's Encrypt rate-limits to 5
duplicates per week.

## Role parameters

| Param                      | Required        | Purpose                                                       |
| -------------------------- | --------------- | ------------------------------------------------------------- |
| `systemd_app_kind`         | yes             | `source` (files from a dir) or `simple` (rendered container). |
| `systemd_app_name`         | yes             | `source`: app dir name. `simple`: container name / DNS name.  |
| `systemd_app_state`        | no              | `present` (default) deploys; `absent` decommissions.          |
| `systemd_app_enable_units` | no              | systemd unit names to enable and start (see below).           |
| `systemd_app_domain`       | no              | Public hostname; when set, a Caddy route is added.            |
| `systemd_app_upstream`     | no              | Upstream container name to proxy to (default `systemd_app_name`). |
| `systemd_app_port`         | no              | Upstream port for the Caddy route (default `8080`).           |
| `systemd_app_data_dirs`    | no              | Bind-mount dirs to pre-create with a given owner (see below). |

### `simple`-kind parameters

| Param                       | Required             | Purpose                                            |
| --------------------------- | -------------------- | -------------------------------------------------- |
| `systemd_app_image`         | yes (simple+present) | Full image ref, e.g. `docker.io/org/app:latest`.   |
| `systemd_app_description`   | no                   | Unit description (default `<name> container`).     |
| `systemd_app_network`       | no                   | Network the container joins (default `web.network`). |
| `systemd_app_env`           | no                   | Env vars rendered as `Environment=` lines.         |
| `systemd_app_volumes`       | no                   | Raw `Volume=` values.                              |
| `systemd_app_publish_ports` | no                   | Raw `PublishPort=` values.                         |
| `systemd_app_container_options` | no               | Raw lines for the `[Container]` section.           |
| `systemd_app_service_options` | no                 | Raw lines for the `[Service]` section.            |
| `systemd_app_health_cmd`    | no                   | Probe command; enables the health block (see below). |

`systemd_app_env` values are quoted and escaped into the unit, so a value with spaces, a
`"` or a `%` needs nothing special at the call site (systemd splits `Environment=` on
whitespace and reads `%` as a specifier, so a bare value would otherwise be truncated or
mangled). Keys have to spell legal variable names — letters, digits and underscore, no
leading digit. A control character in a value, a description, or one of the raw-line lists
is refused rather than written: a newline ends the line and turns whatever follows into
another unit directive, and no quoting fixes that. The raw-line parameters are one Quadlet
line per list entry, which is why an entry may not contain a newline of its own.

## Tunables (defaults)

| Variable                  | Default                      | Purpose                                  |
| ------------------------- | ---------------------------- | ---------------------------------------- |
| `systemd_app_apps_dir`    | `{{ playbook_dir }}/../apps` | Source of `source`-kind app definitions. |
| `systemd_app_system_dir`  | `/etc/containers/systemd`    | Quadlet install dir on the host.         |
| `systemd_app_unit_dir`    | `/etc/systemd/system`        | Plain-unit install dir on the host.      |
| `systemd_app_root`        | `/var/app`                   | Every app's home → `<root>/<app>`.       |
| `systemd_app_caddy_confd` | `/var/app/reverse_proxy/config/conf.d` | Dir for generated route snippets. |

`systemd_app_apps_dir` follows the location of the playbook you invoke, so it moves
when the playbook does. A `source` app whose directory is not there fails the run
before anything is installed, rather than being treated as an app that ships no
files: the lookups that read the directory are globs and return nothing for a path
that does not exist, so a deploy would otherwise install nothing, report success,
and prune every file the last one recorded (see [install manifest](#install-manifest)).

## Install manifest

A deploy records the absolute host paths it installed to
`/var/app/<app>/.install-manifest`, one per line — a `source` app's Quadlet files,
systemd units and every file of its config tree, a `simple` app's one rendered
`<name>.container`. It sits inside the app's own home so the two share
fate: `absent` drops that tree and the manifest goes with it, and a hand-removed or
restored `/var/app/<app>` cannot leave a stale record behind. Both a later deploy and a decommission work from that
file rather than re-deriving from `apps/<app>/`, which by then may name different files
or be gone entirely, so a renamed or deleted file does not linger on the host and a
decommission needs no source tree at all.

The manifest is host state acted on as root, so every recorded path is checked before
anything is removed. A line is legal in exactly two shapes: a single path segment
directly inside `systemd_app_system_dir` / `systemd_app_unit_dir`, or any file nested
under this app's own `/var/app/<app>/config`. `.` and `..` segments are refused in both,
and a manifest containing one illegal line fails the run without deleting anything.

`absent` reads it too, and has to: the Quadlet files and systemd units it must remove
live in `/etc/containers/systemd/` and `/etc/systemd/system/`, which are shared with
every other app and cannot be relocated — systemd and the Quadlet generator only read
those paths. Dropping `/var/app/<app>` alone would leave them behind, and the generator
would recreate the service on the next `daemon-reload`.

**Changing an app's kind converges on it.** Both kinds record a manifest and both
reconcile against it, so flipping `systemd_app_kind` between `source` and `simple` —
keeping the name — prunes whatever the old kind installed and the new one does not. A
`source` app that shipped a sidecar Quadlet, a `.timer` and a config tree becomes a
`simple` app whose only installed path is the rendered `<name>.container`, and the other
three are removed on that converge. The one thing to do by hand is the running units: a
unit whose file is pruned keeps running until it is stopped or the host reboots (see
below), so stop the ones the app no longer ships, once.

Why a manifest and not a destination diff: `/var/app/reverse_proxy/config/` also holds
`conf.d/*.caddy` route snippets generated for *other* apps, which exist nowhere in the
source tree. Deleting whatever is not in `apps/<app>/config/` would wipe every route on
every converge. A manifest only ever removes what a previous deploy recorded installing,
so generated and runtime files are invisible to it.

Only files are reconciled — a pruned tree can leave empty directories behind. And a unit
dropped from the app that is still running keeps running until it is stopped or the host
reboots: remove it from `systemd_app_enable_units` and stop it once by hand. That applies
to a change of kind as much as to a deleted file.

An app last deployed before the role recorded a manifest for its kind has none, so its
first converge under this role prunes nothing and records one — the reconciliation starts
from the next deploy. `absent` covers the gap for a `simple` app in that state by also
removing the rendered `<name>.container` by name.

## Caddy routing

Set `systemd_app_domain` to expose the app through the reverse proxy without
editing the central `Caddyfile`. The role drops a `<systemd_app_name>.caddy`
snippet (`domain → upstream:port`) into `systemd_app_caddy_confd`, which the
Caddyfile imports via `import /etc/caddy/conf.d/*.caddy`. The deploy playbook
reloads Caddy once as a post-task, so the route applies after all apps converge.

`systemd_app_upstream` defaults to `systemd_app_name` — correct for every `simple`
app (the container *is* `<name>`) and for `source` apps whose `ContainerName=`
matches the app name. Override it when a `source` app's routable container is
named differently. On `absent`, the role removes the `<name>.caddy` snippet it
generated.

The three values that compose the snippet are checked before it is written:
`systemd_app_domain` must be a hostname of at least two labels, optionally wildcarded
(`*.example.com`); `systemd_app_upstream` a container name; `systemd_app_port` 1-65535.
The check is there because the failure is not local — the domain becomes the address of a
site block, and the Caddyfile imports *every* app's snippet, so one value carrying a brace,
a comment character or a newline stops Caddy loading any route at all. A typo at the call
site is a failed run instead.

## Making changes take effect

Installing a file is not the same as the app running from it, and the two kinds of change
this role writes become live in different ways.

**A changed unit definition, or a changed secret, needs a new container.** The Quadlet
generator rewrites the `.service` at `daemon-reload`, but systemd does not act on a unit it
merely re-read, and podman reads a secret only when it *creates* a container — so the
running one carries on with the image, options and values it started with. The role
therefore **restarts** the app's managed units when this deploy changed a file that defines
them: anything installed from `quadlet/` or `unit/`, or the rendered `<name>.container` of a
`simple` app.

**A changed config tree only needs the process told.** The files are bind-mounted, so they
are already in place. The role **reloads** the managed units instead, which is why config
is not folded in with the above: a unit that declares `ExecReload=` can take new config
without dropping what it is serving, and cycling it would throw that away. A unit with no
reload action reports `CanReload=no` and is restarted — blunt, but the only way to get the
change into the process. Nothing extra to declare per app: the unit already says which it
is, and the role reads that rather than keeping a second copy of the answer.

A restart supersedes a reload, so a deploy that changed both does one restart. A converge
that changed neither leaves the units alone, so this costs nothing on a no-op run.

Two consequences worth knowing:

- **A restart does not pull.** The pull policy decides that, and the default only fetches
  an image that is missing locally, so bumping `Image=` to a tag already on the host
  reuses it. A new tag or digest is missing by definition and is fetched; a moving tag
  like `:latest` is not, and needs `podman auto-update` (or `Pull=newer`) to move.
- **Removed files are treated by kind.** A pruned config file counts as a config change,
  since a running app is still reading what is now gone. A pruned unit file does not — it
  leaves nothing to act on, and a unit dropped from the app keeps running until it is
  stopped by hand (see [install manifest](#install-manifest)).

Generated route snippets are not part of any app's config tree, so they are outside this
entirely; the deploy playbook applies those centrally once every app has converged (see
[Caddy routing](#caddy-routing)).

## Healthchecks and auto-update rollback

`podman auto-update` reverts an image only when the unit **fails to start**. Without a
healthcheck a container that boots and then serves errors counts as started, so the bad
image stays. `Notify=healthy` closes that gap: systemd withholds "started" until the
first probe passes, which turns a broken image into a failed start that gets rolled back.

For a `simple` app, set `systemd_app_health_cmd` and the role emits the whole block —
`HealthCmd`, `HealthInterval`, `HealthRetries`, `HealthStartPeriod`, `Notify=healthy`,
and a matching `TimeoutStartSec`. Only the command is per-app, because nothing else can
be: an HTTP app wants a request, Postgres wants `pg_isready`. Everything around it is
fleet policy in `defaults/main.yml` and rarely needs overriding.

```yaml
    - role: systemd_app
      systemd_app_kind: simple
      systemd_app_name: nasplan
      systemd_app_health_cmd: "wget -qO /dev/null http://127.0.0.1:8080/ || exit 1"
```

Two things to know. The command runs **inside** the container, so the tool has to exist
in the image — check with `podman exec <name> sh -lc 'command -v wget curl'` before
relying on it, because a probe that can never pass makes the deploy itself fail. And
address the app as `127.0.0.1`, not `localhost`, which resolves to `::1` on musl images
where nothing is listening. Leave `systemd_app_health_cmd` empty for an app that cannot
be probed; it then gets no health block and no rollback protection.

`source` apps declare these keys in their own Quadlet files (see
`apps/whichday/quadlet/whichday.container`), so this policy does not reach them.

## Secrets

`systemd_app_env` renders `Environment=` lines into a Quadlet, which is a unit file and
world-readable — fine for a hostname, wrong for a client secret. Instead, an app that needs
secrets ships one SOPS-encrypted file in its own directory, keyed by the podman secret
names:

```yaml
# apps/whichday/secrets.sops.yaml   (values encrypted; keys readable)
whichday-oidc-issuer-uri: https://accounts.example.com
whichday-oidc-client-id: …
whichday-oidc-client-secret: …
```

Nothing at the call site, for either kind. The role looks for
`apps/<app>/secrets.sops.yaml` the same way it looks for the app's `quadlet/` and
`config/` directories, decrypts it on the controller, and stores each entry in podman's
secret store. The file sits at the app root, outside the three directories the role
installs from, so nothing copies it to the host.

How the container reaches a stored value differs by kind:

**`simple`** — the role renders the reference, deriving the variable from the name:
upper-cased, dashes as underscores. The file above yields

```ini
Secret=whichday-oidc-client-secret,type=env,target=WHICHDAY_OIDC_CLIENT_SECRET
```

so the name has to be the variable the app reads, spelled in lower case with dashes. That
is usually no constraint, since podman secret names are host-global and want an app prefix
anyway — and app-prefixed variables are what most images expect. When an image insists on a
bare name (`POSTGRES_PASSWORD`), the choice is a host-global secret called
`postgres-password` or the `source` kind.

**`source`** — the app writes the line itself and can point any name at any variable:

```ini
# apps/whichday/quadlet/whichday.container
Secret=whichday-oidc-client-secret,type=env,target=WHICHDAY_OIDC_CLIENT_SECRET
```

The name is then the whole contract between file and Quadlet — rename it in one and the
container fails to start referencing a name that no longer exists.

The values are loaded into a single dict, never top-level variables: podman secret names
are not legal variable names, and nothing sensitive becomes a play variable. Each is handed
to podman on **stdin**, never in a command line, since `/proc/<pid>/cmdline` is
world-readable and would expose it to every user on the host for the life of the process.

What this does and does not buy you. The value still ends up in the container's
environment, so it is readable by the process itself, by root, and in `podman inspect` of
the running container. What it avoids is a copy sitting in a `0644` file under
`/etc/containers/systemd/`, in a config tree, or in git. Podman's default file driver keeps
the store in a root-only file, unencrypted — treat "root on the host" as the trust
boundary either way.

**Rotation and renames.** Podman reads a secret when it *creates* a container, and cannot
update a stored one in place on every version this runs on, so the role records a SHA-256
per secret in `/var/app/<app>/.secret-digests` (`0600`, root). A converge where nothing
changed touches nothing. A changed value is removed and re-created, and the app's units are
**restarted** rather than started, since a running container would otherwise keep serving
with the old value. A name dropped from the file — renamed, or deleted — is removed from
the host, the same reconciliation the [install manifest](#install-manifest) does for files.

That record says what was *stored*, not what *is* stored, so a deploy reads the store as
well and re-stores any recorded name podman no longer holds — a secret dropped by hand, or
lost with a store reset. Without that check the digest would still match, nothing would be
re-created, and the app would go on failing to start on a name that is gone, with nothing
to suggest a deploy could fix it.

That record is also how `absent` knows what to remove: secrets are host-global and not part
of `/var/app/<app>`, so they are dropped by name, read from the host rather than from the
encrypted file, which by then may be gone.

**Requirements.** The controller needs the `sops` binary and the `community.sops`
collection (CI installs both; see `ansible/requirements.yml`), and the decryption key —
without it the run fails at the decrypting task, before anything on the host changes. The
host's podman must understand `Secret=` in a Quadlet `[Container]` section; too old and the
generator refuses the unit at `daemon-reload`.

See `ansible/SECRETS.md` for the key itself and how CI gets it.

## Pre-created data directories

A container that writes to a bind mount needs the host directory to exist with the right
owner first: podman creates a missing path as `root:root`, and an image running as a
non-root user then cannot write in it. `systemd_app_data_dirs` creates them before the
container starts.

```yaml
      systemd_app_data_dirs:
        # Relative to /var/app/<app>. 10001 is the uid the image runs as; if upstream
        # changes it, this must follow or the app starts and fails to write.
        - path: data
          owner: "10001"
          group: "10001"
          mode: "0700"
      systemd_app_volumes:
        - "{{ systemd_app_home }}/data:/app/data"
```

A `source` app declares `systemd_app_data_dirs` the same way and writes the matching
`Volume=` line in its own Quadlet, where the host path has to be spelled out in full
(`/var/app/<app>/data:/app/data`) — a static file cannot reference the role's variables.

Paths are relative to the app's own home, so they cannot name another app's: absolute
paths and `..` are refused, since the role creates these as root and `absent` deletes the
tree they live in. Which is the
trade against a named volume: a bind mount here is backed up and restored with the rest
of `/var/app/<app>`, and **destroyed with it** on decommission, where a named volume
would survive.

## Unit names and boot persistence

The role starts/enables its **managed units**: `systemd_app_enable_units` if you
list any, otherwise — for a `simple` app — the single generated `<name>.service`.
A `source` app that lists none starts nothing and relies entirely on its
`[Install]` section (e.g. the network-only `shared` app).

`systemd_app_enable_units` takes the **generated** service name (the `.service`
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
    - role: systemd_app
      systemd_app_kind: source
      systemd_app_name: whichday
      systemd_app_enable_units:
        - whichday.service
      # Optional: route whichday.cloudyhome.org -> whichday:8080 via Caddy.
      systemd_app_domain: whichday.cloudyhome.org
```

A `simple` app — one image behind the reverse proxy, no source dir:

```yaml
    - role: systemd_app
      systemd_app_kind: simple
      systemd_app_name: nasplan
      systemd_app_image: docker.io/binarycodes/make-my-nas:latest
      systemd_app_port: 8080
      systemd_app_domain: nasplan.cloudyhome.org
```

Decommission an app (leave the call in place for one converge, then delete it).
This destroys `/var/app/<app>` — back it up first:

```yaml
    - role: systemd_app
      systemd_app_state: absent
      systemd_app_kind: simple
      systemd_app_name: whoami
```

## Where the logic lives

The role's two pieces of real computation are Python, not Jinja: the secret reconcile and
the input validation are functions in `ansible/filter_plugins/systemd_app.py`, exposed as
the `app_*` filters this role calls. They moved there to be testable —
`ansible/tests/test_systemd_app_filters.py` covers them case by case, and CI runs it before
the lint and the syntax check. A change to what the role *accepts*, or to how it decides
which secrets to store, belongs in that file and its tests rather than in a YAML scalar.
