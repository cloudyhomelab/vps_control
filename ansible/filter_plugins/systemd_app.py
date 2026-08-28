"""Pure functions behind the 'systemd_app' role's Jinja filters.

Every function here replaces a filter chain that used to live inside a YAML scalar. The
move is not cosmetic: a set difference over a dict of digests, or a regex allowlist for a
hostname, is an algorithm, and an algorithm belongs somewhere it can be read, reviewed and
unit-tested. tests/test_systemd_app_filters.py exercises all of it directly, which is what
makes a table of edge cases cheap — verifying the same logic in Jinja meant generating a
playbook and running it.

Filters run on the controller, so nothing here touches a managed host.

One subtlety worth keeping: matching is done with re.fullmatch rather than a '$'-anchored
re.match, because '$' also matches just before a trailing newline. A hostname of
"example.com\\n" would pass a '$' pattern and then break the Caddy site block it composes.
fullmatch has no such edge, so the patterns below carry no end anchor at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

from ansible.errors import AnsibleFilterError

# A podman secret name, and equally a container name: what `podman secret create` and
# `ContainerName=` both accept.
_PODMAN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# The left side of an Environment= assignment, so what a shell would accept as a variable.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A DNS hostname of at least two labels, optionally wildcarded. Two labels because the
# value becomes a Caddy site that will try to get a public certificate for itself, and no
# CA issues one for a single label — so a bare name is a typo, caught here rather than in
# a certificate loop.
_HOSTNAME_RE = re.compile(
    r"(\*\.)?"                                       # optional wildcard label
    r"([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+"   # one or more dotted labels
    r"[A-Za-z]([A-Za-z0-9-]*[A-Za-z0-9])?"           # final label, starting with a letter
)
_HOSTNAME_MAX = 253

# A control character has no representation in a unit file: a newline ends the line and
# turns whatever follows into a further directive, which quoting cannot rescue.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# --- secrets ---------------------------------------------------------------------------

def secret_digests(values):
    """SHA-256 of each secret's value, keyed by secret name.

    Podman offers no version-independent way to read a stored secret back, so a digest is
    what tells the next converge which values actually changed.
    """
    return {
        str(name): hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        for name, value in (values or {}).items()
    }


def reconcile_secrets(digests, recorded_b64="", stored=None):
    """Work out which podman secrets to store and which to drop.

    ``digests`` is this app's secrets as secret_digests returns them, ``recorded_b64``
    the digest file exactly as ``slurp`` hands it over (base64 of JSON, empty when the file
    is not there), and ``stored`` the names podman currently holds.

    Returns ``{store, drop, remove}``. A name is stored again when its value differs from
    the digest recorded for it, when it has no recorded digest at all, or when it is
    recorded yet missing from the store -- dropped by hand, or lost with a store reset,
    which the record alone cannot see. Names the app has stopped declaring are dropped, so
    a rename does not leave the old secret behind with nothing referencing it.

    ``remove`` is what to hand ``podman secret rm``: podman cannot update a stored secret
    in place on every version this may run on and ``create`` refuses an existing name, so a
    rotation is a remove followed by a create, and the same pass clears the drops.
    """
    digests = digests or {}
    recorded = _recorded_digests(recorded_b64)
    stored = set(stored or [])

    changed = {name for name, digest in digests.items() if recorded.get(name) != digest}
    missing = set(digests) - stored
    store = changed | missing
    drop = set(recorded) - set(digests)

    return {"store": sorted(store), "drop": sorted(drop), "remove": sorted(store | drop)}


def _recorded_digests(recorded_b64):
    """The recorded digest file as a dict, empty when there is no file to read."""
    if not recorded_b64:
        return {}
    try:
        raw = base64.b64decode(recorded_b64).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError) as exc:
        raise AnsibleFilterError(f"recorded secret digests are not valid base64 text: {exc}")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise AnsibleFilterError(
            f"recorded secret digests are not valid JSON: {exc}. Remove the app's "
            ".secret-digests file to have the next deploy store every secret afresh."
        )
    if not isinstance(parsed, dict):
        raise AnsibleFilterError(
            f"recorded secret digests must be a JSON object, got {type(parsed).__name__}."
        )
    return parsed


# --- inputs that compose a generated file ----------------------------------------------

def route_problems(domain, upstream=None, port=None):
    """Why this app cannot be routed, one string per problem; empty means it can.

    These three compose a Caddy site block, and the Caddyfile imports every app's snippet,
    so a value carrying a brace, a comment character or a newline does not merely break
    this route -- it stops Caddy loading any of them.
    """
    problems = []

    domain = "" if domain is None else str(domain)
    if not _HOSTNAME_RE.fullmatch(domain):
        problems.append(
            f"systemd_app_domain {domain!r} is not a hostname of at least two labels, "
            "optionally wildcarded as '*.example.com'"
        )
    elif len(domain) > _HOSTNAME_MAX:
        problems.append(
            f"systemd_app_domain is {len(domain)} characters, over the {_HOSTNAME_MAX} maximum"
        )

    upstream = "" if upstream is None else str(upstream)
    if not _PODMAN_NAME_RE.fullmatch(upstream):
        problems.append(
            f"systemd_app_upstream {upstream!r} is not a container name (letters, digits, "
            "dot, dash or underscore, not starting with a dot)"
        )

    try:
        port_number = int(port)
    except (TypeError, ValueError):
        port_number = None
    if port_number is None or not 1 <= port_number <= 65535:
        problems.append(f"systemd_app_port {port!r} is not a port in 1-65535")

    return problems


def container_problems(env, description="", volumes=None, publish_ports=None,
                       container_options=None, service_options=None):
    """Why this app's Quadlet cannot be rendered, one string per problem.

    Values are not checked for spaces, quotes or percent signs: the template quotes and
    escapes those, so they are legal input. Only what cannot survive a unit file at all is
    refused.
    """
    problems = []

    for key, value in (env or {}).items():
        if not _ENV_KEY_RE.fullmatch(str(key)):
            problems.append(
                f"systemd_app_env key {str(key)!r} is not a legal variable name (letters, "
                "digits and underscore, no leading digit)"
            )
        # Reported by key, not by value: a failure message is no place for either, and the
        # key is what the caller has to go and fix.
        if isinstance(value, str) and _CONTROL_RE.search(value):
            problems.append(
                f"the value of systemd_app_env key {str(key)!r} holds a control character"
            )

    if description is not None and _CONTROL_RE.search(str(description)):
        problems.append("systemd_app_description holds a control character")

    raw = (
        ("systemd_app_volumes", volumes),
        ("systemd_app_publish_ports", publish_ports),
        ("systemd_app_container_options", container_options),
        ("systemd_app_service_options", service_options),
    )
    for name, lines in raw:
        for line in lines or []:
            if _CONTROL_RE.search(str(line)):
                problems.append(
                    f"{name} entry {str(line)!r} holds a control character; each entry is "
                    "one Quadlet line, so use a further entry rather than a newline"
                )

    return problems


# --- rendering -------------------------------------------------------------------------

def systemd_env_lines(env):
    """``Environment=`` lines for a Quadlet, quoted and escaped.

    systemd splits ``Environment=`` on whitespace, so a bare value with a space would set
    the variable to its first word and read the rest as further assignments; inside double
    quotes a backslash and a quote need escaping; and '%' opens a specifier unless doubled.
    Quoting and escaping are one rule, not two, so they live together here rather than half
    in a template -- removing the quotes there would silently break the escaping.

    Sorted, so the rendered unit does not change when a call site reorders its env, which
    would otherwise restart the container for nothing.
    """
    normalised = {str(key): str(value) for key, value in (env or {}).items()}
    lines = []
    for key in sorted(normalised):
        value = normalised[key]
        # Unreachable through the role, which validates before rendering (see
        # container_problems); a backstop so the two cannot drift apart.
        if _CONTROL_RE.search(key) or _CONTROL_RE.search(value):
            raise AnsibleFilterError(
                f"systemd_app_env entry {key!r} holds a control character, which cannot be "
                "written to a unit file at any quoting level"
            )
        lines.append(f'Environment="{key}={_escape_in_quotes(value)}"')
    return lines


def _escape_in_quotes(value):
    """A value as it must appear inside a double-quoted systemd directive."""
    return (
        value.replace("\\", "\\\\")   # first, or the escapes added below get doubled
        .replace('"', '\\"')
        .replace("%", "%%")             # a lone '%' would open a specifier
    )


class FilterModule:
    """Filters for the 'systemd_app' role.

    Named for what each one computes rather than for the role that calls them: these are
    headed for a collection, where filters resolve as namespace.collection.name across the
    whole collection and no role can scope its own, so a role prefix would mark ownership
    the FQCN already carries -- and would read wrong the first time a second role uses one.
    """

    def filters(self):
        return {
            "secret_digests": secret_digests,
            "reconcile_secrets": reconcile_secrets,
            "route_problems": route_problems,
            "container_problems": container_problems,
            "systemd_env_lines": systemd_env_lines,
        }
