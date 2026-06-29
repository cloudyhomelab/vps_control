"""Render a GitHub Actions job summary for an Ansible run.

GitHub Actions shows a per-run "Job Summary" built from whatever Markdown a step
appends to the file named by $GITHUB_STEP_SUMMARY (the same mechanism Docker's
build-push-action uses). This aggregate callback appends a compact run summary —
overall status, a per-host PLAY RECAP table, and a highlighted failures section —
so a deploy's outcome is visible at a glance on the run page.

It is an *aggregate* callback, so it runs alongside the normal console output
rather than replacing it. When GITHUB_STEP_SUMMARY is unset (e.g. local runs) the
plugin disables itself, so it is safe to leave enabled everywhere.
"""

from __future__ import annotations

import os
import time

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
  name: github_summary
  type: aggregate
  short_description: Write a GitHub Actions job summary for the Ansible run.
  description:
    - Appends a Markdown run summary (overall status, per-host recap table, and a
      highlighted failures section) to the file named by the GITHUB_STEP_SUMMARY
      environment variable.
    - No-op when GITHUB_STEP_SUMMARY is unset, so it is safe to enable for local
      runs as well as CI.
  requirements:
    - Enable via callbacks_enabled in ansible.cfg.
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "github_summary"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        # Nothing to write to without the GitHub-provided file; stay out of the way.
        self.disabled = not self._summary_path
        self._playbook_name = None
        self._start = time.time()
        # Each entry: (host, task name, message, kind) where kind is failed/unreachable.
        self._failures = []

    def v2_playbook_on_start(self, playbook):
        self._playbook_name = playbook._file_name
        self._start = time.time()

    def v2_runner_on_failed(self, result, ignore_errors=False):
        # Tolerated failures (e.g. the application role's enable-tolerance) are not
        # run failures, so don't surface them as errors.
        if ignore_errors:
            return
        self._failures.append(
            (
                result._host.get_name(),
                result._task.get_name(),
                self._error_message(result._result),
                "failed",
            )
        )

    def v2_runner_on_unreachable(self, result):
        self._failures.append(
            (
                result._host.get_name(),
                result._task.get_name(),
                self._error_message(result._result),
                "unreachable",
            )
        )

    def v2_playbook_on_stats(self, stats):
        if not self._summary_path:
            return

        hosts = sorted(stats.processed.keys())
        summaries = {host: stats.summarize(host) for host in hosts}
        ok = not any(
            s["failures"] or s["unreachable"] for s in summaries.values()
        )

        lines = []
        status = "success" if ok else "failed"
        lines.append(f"## {'✅' if ok else '❌'} Ansible run — {status}")
        lines.append("")
        lines.append(
            f"**Playbook:** `{self._playbook_name or 'unknown'}` · "
            f"**Duration:** {self._format_duration(time.time() - self._start)}"
        )
        lines.append("")
        lines.append("| Host | ok | changed | unreachable | failed | skipped |")
        lines.append("|------|---:|--------:|------------:|-------:|--------:|")
        for host in hosts:
            s = summaries[host]
            lines.append(
                "| {host} | {ok} | {changed} | {unreachable} | {failed} | {skipped} |".format(
                    host=host,
                    ok=s["ok"],
                    changed=self._highlight(s["changed"], "✏️"),
                    unreachable=self._highlight(s["unreachable"], "❌"),
                    failed=self._highlight(s["failures"], "❌"),
                    skipped=s["skipped"],
                )
            )

        if self._failures:
            lines.append("")
            lines.append(f"### ❌ Failures ({len(self._failures)})")
            for host, task, message, kind in self._failures:
                lines.append("")
                lines.append(f"**{host}** · `{task}` _{kind}_")
                lines.append("```")
                lines.append(message.strip() or "(no message)")
                lines.append("```")

        with open(self._summary_path, "a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")

    @staticmethod
    def _highlight(count, marker):
        # Make non-zero changed/failed/unreachable counts stand out in the table.
        return f"**{count}** {marker}" if count else str(count)

    @staticmethod
    def _format_duration(seconds):
        seconds = int(seconds)
        return f"{seconds // 60}m{seconds % 60:02d}s"

    @staticmethod
    def _error_message(result):
        # Prefer the explicit msg; fall back to stderr/stdout/reason so a failure is
        # never reported with an empty body.
        for key in ("msg", "stderr", "stdout", "reason"):
            value = result.get(key)
            if value:
                return value if isinstance(value, str) else str(value)
        return ""
