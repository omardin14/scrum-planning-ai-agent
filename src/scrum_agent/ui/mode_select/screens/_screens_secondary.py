"""Secondary screen builders for mode selection: intake, offline, export, import, team analysis.

# See README: "Architecture" — this module contains rendering functions
# for the intake mode selection, offline sub-menu, export success,
# import file path input, project export success, and team analysis screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.mode_select.screens._screens import _INTAKE_CARDS, _OFFLINE_CARDS, _build_mode_row
from scrum_agent.ui.shared._components import PAD, planning_title

_PAD = PAD  # alias for backward compatibility within this module


def _build_team_analysis_screen(
    profile,
    *,
    scroll_offset: int = 0,
    width: int = 80,
    height: int = 24,
    export_sel: int = 2,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    team_name: str = "",
) -> Panel:
    """Build the team analysis results screen.

    Renders the team profile in a scrollable panel with velocity, calibration,
    story shapes, DoD signals, writing patterns, and export buttons.
    """
    title = planning_title()

    src = profile.source
    key = profile.project_key
    sprints = profile.sample_sprints
    stories = profile.sample_stories

    # Build header: show team name for AzDO, board name for Jira
    board_label = key
    if team_name:
        board_label = f"{team_name} ({key})"
    header_str = f"Team Analysis  \u00b7  {src}/{board_label}  \u00b7  {sprints} sprints  \u00b7  {stories} stories"
    sub = Text(_PAD + header_str, style="bold white", justify="left")

    lines: list = []
    # Track the estimated rendered line count so scrolling works correctly.
    # Text objects are 1 line; Padding(RichTable) objects are header + data rows.
    _rendered_lines = 0

    c_accent = "rgb(100,140,220)"
    c_muted = "rgb(120,120,140)"
    c_value = "bold white"
    c_good = "rgb(80,220,120)"
    c_warn = "rgb(220,180,60)"
    c_bad = "rgb(220,80,80)"
    c_dim = "dim"
    c_example = "rgb(90,90,110)"

    def _add(item, rendered_h: int = 1) -> None:
        """Append an item to lines and track its rendered height."""
        nonlocal _rendered_lines
        lines.append(item)
        _rendered_lines += rendered_h

    def _heading(text: str) -> None:
        _add(Text(""))
        h = Text(_PAD, justify="left")
        h.append(text, style=f"bold {c_accent}")
        _add(h)
        _add(Text(_PAD + "\u2500" * min(len(text), 40), style="rgb(50,60,80)"))

    def _kv(label: str, value: str, val_style: str = c_value) -> None:
        t = Text(_PAD + "  ", justify="left")
        t.append(f"{label:<24s}", style=c_muted)
        t.append(value, style=val_style)
        _add(t)

    def _pct_dots(pct: float, w: int = 15) -> str:
        """Dot-based percentage bar: ●●●●●○○○○○ 45%."""
        filled = round(pct / 100 * w)
        return "\u25cf" * filled + "\u25cb" * (w - filled) + f" {pct:.0f}%"

    _ex = examples or {}

    def _link(ek: str, url: str) -> str:
        """Style string that embeds a terminal hyperlink into the issue key."""
        if url:
            return f"bold underline {c_accent} link {url}"
        return c_accent

    def _show_examples(ekey: str, limit: int = 2) -> None:
        items = _ex.get(ekey, [])
        if not items:
            return
        for ex in items[:limit]:
            t = Text(_PAD + "      ", justify="left")
            ek = ex.get("issue_key", "")
            url = ex.get("issue_url", "")
            summary = ex.get("summary", "")
            detail = ex.get("detail", "")
            if ek:
                t.append(ek, style=_link(ek, url))
            if summary:
                t.append(f"  {summary}", style=c_example)
            if detail:
                t.append(f"  {detail}", style="rgb(70,70,90)")
            _add(t)

    # ── Sprint names (compressed) ─────────────────────────────────────
    if sprint_names:
        import os

        # Strip common prefix to compress "Dev Sprint 1, Dev Sprint 2" → "1, 2"
        names = [n.strip() for n in sprint_names if n.strip()]
        if len(names) >= 2:
            prefix = os.path.commonprefix(names).rstrip("0123456789")
            if len(prefix) > 3:
                short = [n[len(prefix) :].strip() for n in names]
                compressed = f"{prefix.strip()}: {', '.join(short)}"
            else:
                compressed = ", ".join(names)
        elif names:
            compressed = names[0]
        else:
            compressed = ""
        if compressed:
            sp_line = Text(_PAD, justify="left")
            sp_line.append(compressed, style=c_dim)
            _add(sp_line)

    # ── Recurring work (filtered out) ──────────────────────────────
    rec_count = _ex.get("recurring_count", 0)
    del_count = _ex.get("delivery_count", 0)
    rec_items = _ex.get("recurring", [])
    if rec_count and isinstance(rec_count, int) and rec_count > 0:
        _add(Text(""))
        note = Text(_PAD, justify="left")
        note.append(f"{rec_count} recurring tickets excluded ", style=c_muted)
        note.append(f"({del_count} delivery stories analysed)", style=c_dim)
        _add(note)
        if rec_items and isinstance(rec_items, list):
            for ex in rec_items[:3]:
                t = Text(_PAD + "  ", justify="left")
                ek = ex.get("issue_key", "")
                summary = ex.get("summary", "")
                url = ex.get("issue_url", "")
                if ek:
                    t.append(ek, style=_link(ek, url))
                if summary:
                    t.append(f"  {summary}", style=c_example)
                _add(t)

    # ── Team & Velocity ─────────────────────────────────────────────
    team_sz = _ex.get("team_size", 0)
    per_dev_vel = _ex.get("per_dev_velocity", 0)
    members = _ex.get("team_members", [])

    _heading("Team & Velocity")

    if team_sz and isinstance(team_sz, int) and team_sz > 0:
        _kv("Team size", f"{team_sz} contributors", c_value)
        if members and isinstance(members, list):
            m_line = Text(_PAD + "  ", justify="left")
            m_line.append(
                "  \u00b7  ".join(str(m) for m in members[:8]),
                style=c_dim,
            )
            if len(members) > 8:
                m_line.append(f"  +{len(members) - 8} more", style=c_example)
            _add(m_line)

    # Compute velocity from current sprint details so it matches the table,
    # rather than the merged profile which accumulates historical data.
    _sp_details = _ex.get("sprint_details", [])
    if isinstance(_sp_details, list) and _sp_details:
        _sp_pts = [sd["points"] for sd in _sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        vel = round(sum(_sp_pts) / len(_sp_pts), 1) if _sp_pts else profile.velocity_avg
        import math as _m

        if len(_sp_pts) >= 2:
            _mean = sum(_sp_pts) / len(_sp_pts)
            std = round(_m.sqrt(sum((x - _mean) ** 2 for x in _sp_pts) / len(_sp_pts)), 1)
        else:
            std = profile.velocity_stddev
    else:
        vel = profile.velocity_avg
        std = profile.velocity_stddev

    _kv("Team velocity", f"{vel} pts/sprint", c_value)

    # Committed vs delivered from scope timelines
    _vel_scope = _ex.get("scope_changes", {})
    if isinstance(_vel_scope, dict) and _vel_scope.get("totals"):
        _vel_cv = _vel_scope["totals"].get("avg_committed_velocity", 0.0)
        _vel_dv = _vel_scope["totals"].get("avg_delivered_velocity", 0.0)
        if _vel_cv > 0:
            _kv("Committed avg", f"{_vel_cv:g} pts/sprint", c_muted)
            _vel_dp = round(_vel_dv / _vel_cv * 100)
            _vel_ds = c_good if _vel_dp >= 85 else (c_warn if _vel_dp >= 70 else c_bad)
            _kv("Delivered avg", f"{_vel_dv:g} pts/sprint  ({_vel_dp}% accuracy)", _vel_ds)

    if per_dev_vel and isinstance(per_dev_vel, (int, float)) and per_dev_vel > 0:
        _kv("Per developer", f"{per_dev_vel} pts/sprint", c_accent)
    if vel > 0:
        var_pct = std / vel * 100
        var_style = c_good if var_pct < 20 else (c_warn if var_pct < 40 else c_bad)
        _kv("Variance", f"\u00b1{std} ({var_pct:.0f}%)", var_style)

    # Compute completion rate from the current sprint details (not the
    # merged profile which may be dragged down by stale historical data).
    sprint_details = _ex.get("sprint_details", [])
    if isinstance(sprint_details, list) and sprint_details:
        _sp_rates = [sd["rate"] for sd in sprint_details if isinstance(sd, dict) and sd.get("planned", 0) > 0]
        if _sp_rates:
            rate = round(sum(_sp_rates) / len(_sp_rates), 1)
            rate_style = c_good if rate >= 80 else (c_warn if rate >= 60 else c_bad)
            _kv("Completion", _pct_dots(rate), rate_style)
    elif profile.sprint_completion_rate > 0:
        rate = profile.sprint_completion_rate
        rate_style = c_good if rate >= 80 else (c_warn if rate >= 60 else c_bad)
        _kv("Completion", _pct_dots(rate), rate_style)

    if profile.spillover.carried_over_pct > 0:
        sp_pct = profile.spillover.carried_over_pct
        sp_style = c_good if sp_pct < 10 else (c_warn if sp_pct < 20 else c_bad)
        _kv("Spillover", f"{sp_pct}% carried over", sp_style)
        _show_examples("spillover")

    # Velocity trend
    vt = _ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend_label = vt["trend"]
        slope = vt.get("slope", 0)
        first_v = vt.get("first_velocity", 0)
        last_v = vt.get("last_velocity", 0)
        if trend_label == "improving":
            trend_style = c_good
            trend_icon = "\u2197"  # ↗
        elif trend_label == "degrading":
            trend_style = c_bad
            trend_icon = "\u2198"  # ↘
        else:
            trend_style = c_muted
            trend_icon = "\u2192"  # →
        trend_str = f"{trend_icon} {trend_label.capitalize()} ({first_v}\u2192{last_v}, {slope:+.1f}/sprint)"
        _kv("Trend", trend_str, trend_style)

    # ── Sprint Breakdown ───────────────────────────────────────────
    from rich.table import Table as RichTable

    sprint_details = _ex.get("sprint_details", [])
    # Build scope lookup for merging into the breakdown table
    _scope_data = _ex.get("scope_changes", {})
    _scope_sprints = _scope_data.get("per_sprint", []) if isinstance(_scope_data, dict) else []
    _scope_by_name: dict[str, dict] = {s.get("name", ""): s for s in _scope_sprints if isinstance(s, dict)}
    _has_scope = any(s.get("committed_pts") for s in _scope_sprints)

    if sprint_details and isinstance(sprint_details, list) and len(sprint_details) > 0:
        _heading("Sprint Breakdown")

        sp_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 1),
            pad_edge=False,
        )
        sp_table.add_column("Sprint", width=28)
        sp_table.add_column("Pts", justify="right", width=5)
        sp_table.add_column("Done", justify="right", width=6)
        sp_table.add_column("Rate", justify="right", width=6)
        sp_table.add_column("", width=2)
        if _has_scope:
            sp_table.add_column("Scope", justify="right", width=6)
            sp_table.add_column("\u0394", justify="right", width=6)
            sp_table.add_column("Churn", justify="right", width=5)

        for sd in sprint_details:
            if not isinstance(sd, dict):
                continue
            name = sd.get("name", "?")
            pts = sd.get("points", 0)
            planned = sd.get("planned", 0)
            completed = sd.get("completed", 0)
            rate = sd.get("rate", 0)
            done = sd.get("done", False)

            rate_style = c_good if rate >= 80 else (c_warn if rate >= 50 else c_bad)
            has_shadow = sd.get("has_shadow", False)
            if done:
                icon = Text("\u2713", style=c_good)
            elif has_shadow:
                icon = Text("\u25cb", style=c_warn)
            else:
                icon = Text("\u2717", style=c_bad)

            row_cells: list[Text | str] = [
                Text(name[:28], style=c_value),
                Text(str(pts), style=c_muted),
                Text(f"{completed}/{planned}", style=c_muted),
                Text(f"{rate}%", style=rate_style),
                icon,
            ]

            if _has_scope:
                sc = _scope_by_name.get(name, {})
                c_pts = sc.get("committed_pts", 0)
                if c_pts:
                    delta = sc.get("scope_change_total", 0)
                    delta_str = f"+{delta:g}" if delta > 0 else f"{delta:g}"
                    d_sty = c_good if delta == 0 else (c_warn if abs(delta) < 5 else c_bad)
                    churn = sc.get("scope_churn", 0)
                    ch_sty = c_good if churn < 0.1 else (c_warn if churn < 0.3 else c_bad)
                    row_cells.extend(
                        [
                            Text(f"{c_pts:g}\u2192{sc.get('final_pts', 0):g}", style=c_muted),
                            Text(delta_str, style=d_sty),
                            Text(f"{churn:.0%}", style=ch_sty),
                        ]
                    )
                else:
                    row_cells.extend([Text("\u2014", style=c_dim)] * 3)

            sp_table.add_row(*row_cells)

        _add(Padding(sp_table, (0, 0, 0, len(_PAD) + 2)), rendered_h=sp_table.row_count + 1)

        # Analysis of incomplete sprints
        incomplete_sprints = [
            sd
            for sd in sprint_details
            if isinstance(sd, dict)
            and (not sd.get("done", False) or sd.get("has_shadow", False))
            and sd.get("incomplete")
        ]
        if incomplete_sprints:
            _add(Text(""))
            _add(
                Text(
                    _PAD + "  Incomplete sprint analysis:",
                    style=c_muted,
                    justify="left",
                )
            )
            for sd in incomplete_sprints[:3]:
                name = sd.get("name", "?")
                planned = sd.get("planned", 0)
                completed = sd.get("completed", 0)
                gap = planned - completed
                inc = sd.get("incomplete", [])

                _add(Text(""))
                hdr = Text(_PAD + "    ", justify="left")
                has_sh = sd.get("has_shadow", False)
                hdr.append(name, style=c_warn)
                if gap > 0:
                    hdr.append(
                        f"  {gap} stories not completed",
                        style=c_muted,
                    )
                if has_sh:
                    hdr.append(
                        "  + shadow spillover" if gap > 0 else "  shadow spillover",
                        style=c_warn,
                    )
                _add(hdr)

                for item in inc[:2]:
                    if not isinstance(item, dict):
                        continue
                    t = Text(_PAD + "      ", justify="left")
                    ek = item.get("issue_key", "")
                    i_url = item.get("issue_url", "")
                    sm = item.get("summary", "")
                    pts_v = item.get("points", 0)
                    if ek:
                        t.append(ek, style=_link(ek, i_url))
                    if sm:
                        t.append(f"  {sm}", style=c_example)
                    if item.get("shadow"):
                        t.append("  (re-created)", style=c_warn)
                    elif pts_v:
                        t.append(f"  ({pts_v}pts)", style=c_dim)
                    _add(t)

    # ── Shadow Spillover ───────────────────────────────────────────
    shadow = _ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        _add(Text(""))
        hdr = Text(_PAD + "  ", justify="left")
        hdr.append(
            f"\u26a0 {len(shadow)} re-created stories detected",
            style=f"bold {c_warn}",
        )
        _add(hdr)
        _add(
            Text(
                _PAD + "  Closed in one sprint but re-created in the next:",
                style=c_muted,
                justify="left",
            )
        )
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            t = Text(_PAD + "    ", justify="left")
            ek = sh.get("issue_key", "")
            url = sh.get("issue_url", "")
            sh_title = sh.get("title", "")
            from_sp = sh.get("from_sprint", "")
            to_sp = sh.get("to_sprint", "")
            if ek:
                t.append(ek, style=_link(ek, url))
            if sh_title:
                t.append(f"  {sh_title}", style=c_example)
            _add(t)
            if from_sp or to_sp:
                m = Text(_PAD + "      ", justify="left")
                m.append(f"{from_sp} \u2192 {to_sp}", style=c_dim)
                _add(m)

    # ── Scope Analysis (integrated into Sprint Breakdown) ───────────
    scope = _ex.get("scope_changes", {})
    if isinstance(scope, dict) and scope.get("totals"):
        totals = scope["totals"]
        t_added = totals.get("added_mid_sprint", 0)
        t_re_est = totals.get("re_estimated", 0)
        t_total = totals.get("total_stories", 0)
        avg_committed = totals.get("avg_committed_velocity", 0.0)
        avg_delivered = totals.get("avg_delivered_velocity", 0.0)

        has_data = t_added > 0 or t_re_est > 0 or avg_committed > 0
        if has_data:
            _add(Text(""))
            # Committed → Delivered summary
            if avg_committed > 0:
                delivery_pct = round(avg_delivered / avg_committed * 100)
                d_sty = c_good if delivery_pct >= 85 else (c_warn if delivery_pct >= 70 else c_bad)
                summary = Text(_PAD + "  ", justify="left")
                summary.append("Committed ", style=c_muted)
                summary.append(f"{avg_committed:g}", style="bold " + c_value)
                summary.append(" \u2192 Delivered ", style=c_muted)
                summary.append(f"{avg_delivered:g}", style="bold " + c_value)
                summary.append("  pts/sprint avg  ", style=c_muted)
                summary.append(f"({delivery_pct}% accuracy)", style=d_sty)
                _add(summary)

            # Added/re-estimated stats
            if t_total > 0 and (t_added > 0 or t_re_est > 0):
                add_pct = round(t_added / t_total * 100)
                re_pct = round(t_re_est / t_total * 100)
                add_sty = c_good if add_pct < 10 else (c_warn if add_pct < 25 else c_bad)
                re_sty = c_good if re_pct < 10 else (c_warn if re_pct < 25 else c_bad)
                stats = Text(_PAD + "  ", justify="left")
                stats.append(f"{t_added} added mid-sprint ", style=add_sty)
                stats.append(f"({add_pct}%)", style=c_dim)
                stats.append("  \u00b7  ", style=c_dim)
                stats.append(f"{t_re_est} re-estimated ", style=re_sty)
                stats.append(f"({re_pct}%)", style=c_dim)
                _add(stats)

            # Per-sprint scope narratives (most recent sprints with changes)
            timelines = scope.get("timelines", [])
            sprints_with_events = [tl for tl in timelines if hasattr(tl, "change_events") and tl.change_events]
            for tl in sprints_with_events[-4:]:  # most recent 4
                _add(Text(""))
                delta = tl.scope_change_total
                pct = round(delta / tl.committed_pts * 100) if tl.committed_pts else 0
                delta_str = f"+{delta:g}" if delta > 0 else f"{delta:g}"
                d_sty_n = c_good if delta == 0 else (c_warn if abs(delta) < 5 else c_bad)
                hdr = Text(_PAD + "  ", justify="left")
                hdr.append(tl.sprint_name, style="bold " + c_value)
                hdr.append(f"  {delta_str} scope ", style=d_sty_n)
                hdr.append(f"({pct:+d}%)", style=c_dim)
                _add(hdr)

                # Day 1 committed
                n_stories = len(tl.daily_snapshots[0].stories_in_sprint) if tl.daily_snapshots else 0
                day1 = Text(_PAD + "    ", justify="left")
                day1.append(f"committed {tl.committed_pts:g} pts", style=c_muted)
                if n_stories:
                    day1.append(f" ({n_stories} stories)", style=c_dim)
                _add(day1)

                # Events (max 5)
                for ev in tl.change_events[:5]:
                    ct_short = ev.change_type.replace("re_estimated_", "re-est ")
                    ct_short = ct_short.replace("_", " ")
                    delta_s = f"+{ev.delta_pts:g}" if ev.delta_pts > 0 else f"{ev.delta_pts:g}"
                    ev_sty = c_good if ev.delta_pts < 0 else (c_warn if abs(ev.delta_pts) <= 3 else c_bad)
                    ct_sty = "#22c55e" if "removed" in ct_short else ("#ef4444" if "added" in ct_short else c_warn)
                    row = Text(_PAD + "    ", justify="left")
                    row.append(f"{delta_s} pts", style=ev_sty)
                    row.append(f"  {ev.issue_key}", style=c_accent)
                    row.append(f"  {ct_short}", style=ct_sty)
                    if ev.summary:
                        row.append(f"  {ev.summary[:45]}", style=c_dim)
                    _add(row)
                if len(tl.change_events) > 5:
                    more = Text(_PAD + "    ", justify="left")
                    more.append(f"... +{len(tl.change_events) - 5} more", style=c_dim)
                    _add(more)

                # Final/delivered
                n_final = len(tl.daily_snapshots[-1].stories_in_sprint) if tl.daily_snapshots else 0
                foot = Text(_PAD + "    ", justify="left")
                foot.append(f"final {tl.final_pts:g} pts", style=c_muted)
                if n_final:
                    foot.append(f" ({n_final} stories)", style=c_dim)
                foot.append(f" \u00b7 delivered {tl.delivered_pts:g} pts", style=c_muted)
                _add(foot)

            # Carry-over chains
            chains = scope.get("carry_over_chains", [])
            if chains:
                _add(Text(""))
                h = Text(_PAD + "  ", justify="left")
                h.append(
                    f"\u26a0 {len(chains)} stories bounced across 3+ sprints",
                    style=f"bold {c_warn}",
                )
                _add(h)
                for ch in chains[:5]:
                    if not isinstance(ch, dict):
                        continue
                    t = Text(_PAD + "    ", justify="left")
                    ek = ch.get("issue_key", "")
                    sc = ch.get("sprint_count", 0)
                    sprints = ch.get("sprints", [])
                    t.append(ek, style=c_accent)
                    t.append(f"  {sc} sprints: ", style=c_muted)
                    t.append(" \u2192 ".join(str(s) for s in sprints), style=c_dim)
                    _add(t)

    # ── Spillover Root Causes ─────────────────────────────────────
    spill_corr = _ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_disc = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        # Only show if there's meaningful spillover in any dimension
        has_spill = any(v > 0 for d in (by_size, by_disc, by_tasks) if isinstance(d, dict) for v in d.values())
        if has_spill:
            _heading("Spillover Root Causes")
            if by_size:
                row = Text(_PAD + "  ", justify="left")
                row.append("By size:       ", style=c_muted)
                parts = []
                for sz, pct in sorted(by_size.items(), key=lambda x: int(x[0])):
                    sty = c_good if pct < 10 else (c_warn if pct < 25 else c_bad)
                    parts.append((f"{sz}pt={pct:.0f}%", sty))
                for i, (txt, sty) in enumerate(parts):
                    if i > 0:
                        row.append("  ", style=c_dim)
                    row.append(txt, style=sty)
                _add(row)
            if by_disc:
                row = Text(_PAD + "  ", justify="left")
                row.append("By discipline: ", style=c_muted)
                parts = []
                for disc, pct in sorted(by_disc.items()):
                    sty = c_good if pct < 10 else (c_warn if pct < 25 else c_bad)
                    parts.append((f"{disc}={pct:.0f}%", sty))
                for i, (txt, sty) in enumerate(parts):
                    if i > 0:
                        row.append("  ", style=c_dim)
                    row.append(txt, style=sty)
                _add(row)
            if by_tasks:
                row = Text(_PAD + "  ", justify="left")
                row.append("By tasks:      ", style=c_muted)
                parts = []
                for bucket, pct in by_tasks.items():
                    sty = c_good if pct < 10 else (c_warn if pct < 25 else c_bad)
                    parts.append((f"{bucket}={pct:.0f}%", sty))
                for i, (txt, sty) in enumerate(parts):
                    if i > 0:
                        row.append("  ", style=c_dim)
                    row.append(txt, style=sty)
                _add(row)

    # ── Discipline-Specific Calibration ───────────────────────────
    disc_cal = _ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        _heading("Calibration by Discipline")
        _add(
            Text(
                _PAD + "  Cycle time + variance per discipline and point value",
                style="rgb(80,80,100)",
                justify="left",
            )
        )
        for disc, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            _add(Text(""))
            h = Text(_PAD + "  ", justify="left")
            h.append(disc, style=f"bold {c_accent}")
            _add(h)
            for e in entries:
                if not isinstance(e, dict):
                    continue
                pts = e.get("points", 0)
                avg_d = e.get("avg_cycle_days", 0)
                var = e.get("variance", 0)
                samples = e.get("samples", 0)
                sp = e.get("spill_pct", 0)
                pts_label = f"{pts}pt" if pts == 1 else f"{pts}pts"
                row = Text(_PAD + "    ", justify="left")
                row.append(f"{pts_label:<6s}", style=c_muted)
                day_sty = c_value if avg_d <= 15 else (c_warn if avg_d <= 40 else c_bad)
                row.append(f"{avg_d:.0f}d", style=day_sty)
                if var > 0:
                    var_sty = c_good if var < 3 else (c_warn if var < 8 else c_bad)
                    row.append(f" \u00b1{var:.0f}d", style=var_sty)
                row.append(f"  {samples} samples", style=c_dim)
                if sp > 10:
                    row.append(f"  {sp:.0f}% spill", style=c_warn)
                _add(row)

    # ── What Each Point Value Means ─────────────────────────────────

    cals_with_data = [c for c in profile.point_calibrations if c.sample_count > 0]
    if cals_with_data:
        _heading("What Each Point Value Means")
        _add(
            Text(
                _PAD + "  Based on this team's historical data",
                style="rgb(80,80,100)",
                justify="left",
            )
        )

        for cal in cals_with_data:
            days = cal.avg_cycle_time_days
            pts_label = f"{cal.point_value} pt" if cal.point_value == 1 else f"{cal.point_value} pts"
            day_style = c_value if days <= 15 else (c_warn if days <= 40 else c_bad)

            _add(Text(""))
            # Point value header with key stats
            h = Text(_PAD + "  ", justify="left")
            h.append(pts_label, style=f"bold {c_accent}")
            h.append(f"   {days:.0f}d avg cycle", style=day_style)
            h.append(f"  \u00b7  {cal.sample_count} stories", style=c_muted)
            if cal.typical_task_count > 0:
                h.append(f"  \u00b7  ~{cal.typical_task_count:.0f} tasks", style=c_muted)
            # Confidence label
            conf_levels = _ex.get("confidence_levels", {})
            conf = conf_levels.get(cal.point_value, "") if isinstance(conf_levels, dict) else ""
            if conf == "high":
                h.append("  \u00b7  HIGH confidence", style=c_good)
            elif conf == "low":
                h.append("  \u00b7  low confidence", style=c_warn)
            _add(h)

            # Common patterns — what kind of work this point value represents
            if cal.common_patterns:
                p = Text(_PAD + "    ", justify="left")
                p.append("Typical work: ", style=c_muted)
                p.append(", ".join(cal.common_patterns), style=c_value)
                _add(p)

            # Representative examples
            ex_items = _ex.get(f"calibration_{cal.point_value}pt", [])
            if ex_items:
                for ex in ex_items[:3]:
                    t = Text(_PAD + "    ", justify="left")
                    ek = ex.get("issue_key", "")
                    url = ex.get("issue_url", "")
                    summary = ex.get("summary", "")
                    detail = ex.get("detail", "")
                    if ek:
                        t.append(ek, style=_link(ek, url))
                    if summary:
                        t.append(f"  {summary}", style=c_example)
                    if detail:
                        t.append(f"  {detail}", style="rgb(70,70,90)")
                    _add(t)

        _add(Text(""))

    # ── Story Shape by Discipline ─────────────────────────────────────
    shapes = profile.story_shapes
    real_shapes = [s for s in shapes if s.discipline != "fullstack" or len(shapes) > 1]
    real_shapes = [s for s in real_shapes if s.sample_count > 0]
    if real_shapes:
        _heading("Story Shape by Discipline")
        for shape in real_shapes:
            row = Text(_PAD + "  ", justify="left")
            row.append(f"{shape.discipline:<14s}", style=c_value)
            parts = [f"avg {shape.avg_points} pts"]
            if shape.avg_ac_count > 0:
                parts.append(f"{shape.avg_ac_count} ACs")
            if shape.avg_task_count > 0:
                parts.append(f"{shape.avg_task_count} tasks")
            row.append(" \u00b7 ".join(parts), style=c_muted)
            if shape.sample_count < 5:
                row.append(f"  ({shape.sample_count} samples)", style=c_warn)
            _add(row)

    # ── Task Decomposition ─────────────────────────────────────────
    td = _ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_tasks", 0) > 0:
        _heading("Task Decomposition")

        _kv("Stories with tasks", f"{td['stories_with_tasks']} / {td['total_stories']}")
        _kv("Total tasks", str(td["total_tasks"]))
        _kv("Avg tasks/story", str(td["avg_tasks_per_story"]))
        _kv(
            "Task completion",
            _pct_dots(td["task_completion_rate"]),
            c_good if td["task_completion_rate"] >= 80 else (c_warn if td["task_completion_rate"] >= 50 else c_bad),
        )

        # Type distribution as a table
        type_dist = td.get("type_distribution", {})
        if type_dist:
            _add(Text(""))
            for cat, pct in type_dist.items():
                row = Text(_PAD + "    ", justify="left")
                row.append(f"{cat:<16s}", style=c_value)
                row.append(_pct_dots(pct, w=10), style=c_muted)
                _add(row)

        # Bottlenecks
        bottlenecks = td.get("bottlenecks", [])
        if bottlenecks:
            _add(Text(""))
            for cat, rate, count in bottlenecks:
                t = Text(_PAD + "  ", justify="left")
                t.append(f"\u26a0 {cat}", style=f"bold {c_warn}")
                t.append(
                    f"  only {rate}% completion ({count} tasks)",
                    style=c_muted,
                )
                _add(t)

        # Common recurring tasks
        common_tasks = td.get("common_tasks", [])
        if common_tasks:
            _add(Text(""))
            _add(
                Text(
                    _PAD + "  Common task patterns:",
                    style=c_muted,
                    justify="left",
                )
            )
            for title, cnt in common_tasks[:4]:
                t = Text(_PAD + "    ", justify="left")
                t.append(f"{title[:45]}", style=c_example)
                t.append(f"  \u00d7{cnt}", style=c_dim)
                _add(t)

        # Task assignee spread
        assignees = td.get("task_assignees", {})
        if assignees:
            _add(Text(""))
            _add(
                Text(
                    _PAD + "  Task assignees:",
                    style=c_muted,
                    justify="left",
                )
            )
            for name, cnt in list(assignees.items())[:5]:
                t = Text(_PAD + "    ", justify="left")
                t.append(f"{name:<20s}", style=c_value)
                t.append(f"{cnt} tasks", style=c_muted)
                _add(t)

    # ── Definition of Done ────────────────────────────────────────────
    dod = profile.dod_signal
    dod_items: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_items.append(("Testing", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_items.append(("PR linked", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_items.append(("Code review", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_items.append(("Deploy", dod.stories_with_deploy_mention_pct, "dod_deploy"))

    if dod_items:
        _heading("Definition of Done (inferred)")

        dod_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 2),
            pad_edge=False,
        )
        dod_table.add_column("Practice", width=14)
        dod_table.add_column("Coverage", width=30)
        dod_table.add_column("Example", width=30)

        for label, pct, ekey in dod_items:
            bar_style = c_good if pct >= 50 else (c_warn if pct >= 20 else c_muted)
            ex_items = _ex.get(ekey, [])
            ex_text = Text("", style=c_example)
            if ex_items:
                ex0 = ex_items[0]
                ek = ex0.get("issue_key", "")
                eu = ex0.get("issue_url", "")
                sm = ex0.get("summary", "")[:30]
                if ek:
                    ex_text.append(f"{ek} ", style=_link(ek, eu))
                ex_text.append(sm, style=c_example)

            dod_table.add_row(
                Text(label, style=c_value),
                Text(_pct_dots(pct), style=bar_style),
                ex_text,
            )

        _add(Padding(dod_table, (0, 0, 0, len(_PAD) + 2)), rendered_h=dod_table.row_count + 1)

        if dod.common_checklist_items:
            _add(Text(""))
            items_joined = ", ".join(dod.common_checklist_items[:4])
            _kv("Common signals", items_joined, c_muted)

    # ── Proposed Definition of Done ────────────────────────────────────
    proposed_dod = _ex.get("proposed_dod", {})
    if isinstance(proposed_dod, dict) and proposed_dod.get("items"):
        _heading("Proposed Definition of Done")
        dod_summary = proposed_dod.get("summary", "")
        dod_health = proposed_dod.get("health", "weak")
        if dod_summary:
            h_style = c_good if dod_health == "strong" else (c_warn if dod_health == "moderate" else c_bad)
            _add(Text(_PAD + "  " + dod_summary, style=h_style, justify="left"))

        pdod_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 1),
            pad_edge=False,
        )
        pdod_table.add_column("Practice", width=20)
        pdod_table.add_column("", width=12)
        pdod_table.add_column("Evidence", width=24)
        pdod_table.add_column("Action", ratio=1, no_wrap=True)

        _st_style = {"established": c_good, "emerging": c_warn, "missing": c_bad}
        _st_icon = {"established": "\u2713", "emerging": "\u25cb", "missing": "\u2717"}
        for item in proposed_dod["items"]:
            st = item.get("status", "missing")
            sig = item.get("signals", "no evidence")
            pdod_table.add_row(
                Text(item.get("practice", ""), style=c_value),
                Text(f"{_st_icon.get(st, '?')} {st}", style=_st_style.get(st, c_dim)),
                Text(sig, style=c_muted),
                Text(item.get("recommendation", "")[:55], style=c_dim),
            )
        _add(Padding(pdod_table, (0, 0, 0, len(_PAD) + 2)), rendered_h=len(proposed_dod["items"]) + 1)

        # DoD ordering (typical sequence)
        dod_ordering = proposed_dod.get("ordering", [])
        if len(dod_ordering) >= 2:
            ord_row = Text(_PAD + "  ", justify="left")
            ord_row.append("Typical order: ", style=c_muted)
            ord_row.append(" \u2192 ".join(dod_ordering), style=c_value)
            _add(ord_row)

        # Custom DoD steps (team-specific patterns)
        custom_steps = proposed_dod.get("custom_steps", [])
        if custom_steps:
            _add(Text(""))
            cs_row = Text(_PAD + "  ", justify="left")
            cs_row.append("Team-specific steps: ", style=c_muted)
            cs_parts = [f'"{cs["title"]}" ({cs["pct"]}%)' for cs in custom_steps[:4]]
            cs_row.append(", ".join(cs_parts), style=c_value)
            _add(cs_row)

    # ── Writing Patterns ──────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_items: list[tuple[str, str, str]] = []
    if wp.uses_given_when_then:
        wp_items.append(("AC format", "Given/When/Then \u2713", c_good))
    if wp.median_ac_count > 0:
        wp_items.append(("Median ACs/story", str(wp.median_ac_count), c_value))
    if wp.median_task_count_per_story > 0:
        wp_items.append(("Median tasks/story", str(wp.median_task_count_per_story), c_value))
    if wp.subtask_label_distribution:
        parts = [f"{lbl} {int(pct * 100)}%" for lbl, pct in wp.subtask_label_distribution[:4]]
        wp_items.append(("Sub-task types", " \u00b7 ".join(parts), c_muted))
    if wp.common_personas:
        wp_items.append(("Personas", ", ".join(wp.common_personas[:5]), c_muted))

    if wp_items:
        _heading("Writing Patterns")
        for wp_label, wp_val, wp_sty in wp_items:
            _kv(wp_label, wp_val, wp_sty)

    # ── Epic Sizing ───────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        _heading("Epic Sizing")
        _kv("Avg stories/epic", f"{epic.avg_stories_per_epic:.0f}")
        _kv("Avg points/epic", f"{epic.avg_points_per_epic:.0f}")
        lo, hi = epic.typical_story_count_range
        if lo > 0 or hi > 0:
            _kv("Story count range", f"{lo}\u2013{hi}")

    # ── Recommendations ─────────────────────────────────────────────
    recs: list[tuple[str, str]] = []  # (icon+label, recommendation text)

    if vel > 0:
        var_pct = std / vel * 100
        if var_pct > 35:
            recs.append(
                (
                    "\u26a0 High velocity variance",
                    f"Velocity swings \u00b1{var_pct:.0f}% sprint-to-sprint. "
                    "Consider smaller stories, stricter sprint commitments, "
                    "or capacity planning to stabilise throughput.",
                )
            )

    if profile.sprint_completion_rate > 0 and profile.sprint_completion_rate < 60:
        recs.append(
            (
                "\u26a0 Low sprint completion",
                f"Only {profile.sprint_completion_rate:.0f}% of planned work "
                "completes each sprint. Right-size sprint commitments to "
                "80\u201390% of historical velocity.",
            )
        )

    if profile.spillover.carried_over_pct > 15:
        recs.append(
            (
                "\u26a0 Frequent spillover",
                f"{profile.spillover.carried_over_pct:.0f}% of stories carry "
                "over. Break large stories into smaller slices and "
                "set WIP limits to improve flow.",
            )
        )

    for cal in cals_with_data:
        if cal.point_value >= 8 and cal.avg_cycle_time_days > 60:
            recs.append(
                (
                    f"\u26a0 {cal.point_value}-point stories too large",
                    f"8-point stories take {cal.avg_cycle_time_days:.0f}d "
                    "on average. Consider splitting into 3+5 or "
                    "two 5-point stories for faster feedback.",
                )
            )
            break

    if dod.stories_with_pr_link_pct < 20 and dod.stories_with_pr_link_pct > 0:
        recs.append(
            (
                "\u2139 Low PR linkage",
                f"Only {dod.stories_with_pr_link_pct:.0f}% of stories "
                "reference a pull request. Link PRs to tickets for "
                "traceability and automated DoD.",
            )
        )

    if dod.stories_with_testing_mention_pct < 15 and dod.stories_with_testing_mention_pct > 0:
        recs.append(
            (
                "\u2139 Testing rarely mentioned",
                f"Only {dod.stories_with_testing_mention_pct:.0f}% of stories "
                "mention testing. Add explicit test criteria to "
                "acceptance criteria for quality visibility.",
            )
        )

    if rec_count and isinstance(rec_count, int):
        total = rec_count + (del_count if isinstance(del_count, int) else 0)
        if total > 0 and rec_count / total > 0.3:
            recs.append(
                (
                    "\u2139 High recurring overhead",
                    f"{rec_count} of {total} tickets "
                    f"({rec_count / total * 100:.0f}%) are recurring/ceremony. "
                    "This limits delivery capacity. Consider consolidating "
                    "or timeboxing recurring work.",
                )
            )

    if team_sz and isinstance(team_sz, int) and team_sz > 0:
        if per_dev_vel and isinstance(per_dev_vel, (int, float)) and per_dev_vel < 3:
            recs.append(
                (
                    "\u2139 Low per-developer output",
                    f"Each developer averages {per_dev_vel} pts/sprint. "
                    "Check for blockers, excessive context-switching, "
                    "or stories sized too large.",
                )
            )

    _repos = _ex.get("repositories", {})
    if isinstance(_repos, dict):
        for sr in _repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append(
                    (
                        f"\u26a0 {sr['repo']} has high spillover",
                        f"{sr['spill_rate']}% of stories touching "
                        f"{sr['repo']} don't complete the sprint. "
                        "This repo may have long review cycles, "
                        "difficult deployments, or complex integration work.",
                    )
                )

    _shadow = _ex.get("shadow_spillover", [])
    if isinstance(_shadow, list) and len(_shadow) >= 2:
        recs.append(
            (
                "\u26a0 Shadow spillover",
                f"{len(_shadow)} stories were closed then re-created "
                "in the next sprint. This masks true spillover — "
                "consider keeping the original ticket open and moving "
                "it to the next sprint instead of cloning.",
            )
        )

    td = _ex.get("task_decomposition", {})
    if isinstance(td, dict):
        if td.get("task_completion_rate", 100) < 60:
            recs.append(
                (
                    "\u26a0 Low task completion",
                    f"Only {td['task_completion_rate']}% of sub-tasks "
                    "are completed. Incomplete tasks indicate stories "
                    "are being closed prematurely or tasks are stale.",
                )
            )
        for cat, rate, count in td.get("bottlenecks", []):
            recs.append(
                (
                    f"\u26a0 {cat} bottleneck",
                    f"{cat} tasks have only {rate}% completion "
                    f"({count} tasks). This suggests {cat.lower()} "
                    "is being skipped or deprioritised.",
                )
            )
        sw = td.get("stories_with_tasks", 0)
        tot = td.get("total_stories", 0)
        if tot > 10 and sw > 0 and sw / tot < 0.3:
            recs.append(
                (
                    "\u2139 Low task breakdown",
                    f"Only {sw} of {tot} stories "
                    f"({sw / tot * 100:.0f}%) have sub-tasks. "
                    "Breaking stories into tasks improves visibility "
                    "and helps the team track progress.",
                )
            )

    # Scope change recommendations
    scope = _ex.get("scope_changes", {})
    if isinstance(scope, dict) and scope.get("totals"):
        _sc_totals = scope["totals"]
        _sc_total = _sc_totals.get("total_stories", 0)
        _sc_committed = _sc_totals.get("avg_committed_velocity", 0.0)
        _sc_delivered = _sc_totals.get("avg_delivered_velocity", 0.0)
        if _sc_committed > 0 and _sc_delivered / _sc_committed < 0.7:
            _del_pct = round(_sc_delivered / _sc_committed * 100)
            recs.append(
                (
                    "\u26a0 Low delivery accuracy",
                    f"Team delivers only {_del_pct}% of committed scope "
                    f"({_sc_delivered} of {_sc_committed} pts avg). "
                    "Reduce sprint commitments to match actual capacity.",
                )
            )
        if _sc_total > 0:
            _sc_added = _sc_totals.get("added_mid_sprint", 0)
            _sc_re_est = _sc_totals.get("re_estimated", 0)
            if _sc_added / _sc_total > 0.15:
                recs.append(
                    (
                        "\u26a0 High mid-sprint scope additions",
                        f"{_sc_added} of {_sc_total} stories "
                        f"({_sc_added / _sc_total * 100:.0f}%) "
                        "were added after the sprint started. "
                        "Protect sprint commitments by locking scope after planning.",
                    )
                )
            if _sc_re_est / _sc_total > 0.15:
                recs.append(
                    (
                        "\u26a0 Frequent re-estimation",
                        f"{_sc_re_est} of {_sc_total} stories "
                        f"({_sc_re_est / _sc_total * 100:.0f}%) "
                        "had their points changed mid-sprint. "
                        "Improve estimation accuracy with team calibration sessions.",
                    )
                )
        # High scope churn
        scope_sprints = scope.get("per_sprint", [])
        high_churn = [s for s in scope_sprints if s.get("scope_churn", 0) > 0.3]
        if len(high_churn) >= 2:
            names = ", ".join(s.get("name", "?") for s in high_churn[:3])
            recs.append(
                (
                    "\u26a0 High scope churn",
                    f"{len(high_churn)} sprints had >30% scope churn ({names}). "
                    "Scope is volatile — enforce a sprint lock after planning.",
                )
            )
        chains = scope.get("carry_over_chains", [])
        if len(chains) >= 3:
            recs.append(
                (
                    "\u26a0 Carry-over chains",
                    f"{len(chains)} stories bounced across 3+ sprints. These are zombie stories — split or kill them.",
                )
            )

    # DoD recommendation
    _pdod = _ex.get("proposed_dod", {})
    if isinstance(_pdod, dict) and _pdod.get("health") == "weak":
        _pdod_items = _pdod.get("items", [])
        _missing = [i["practice"] for i in _pdod_items if i.get("status") == "missing"]
        _missing_str = ", ".join(_missing[:3]) if _missing else "most practices"
        recs.append(
            (
                "\u26a0 No consistent Definition of Done",
                f"The analysis could not find a consistent DoD for this team. "
                f"{_missing_str} show no evidence of being practiced. "
                "Create a team DoD checklist — even a simple one (code reviewed, "
                "tests passing, deployed to staging) dramatically improves quality.",
            )
        )
    elif isinstance(_pdod, dict) and _pdod.get("health") == "moderate":
        _pdod_items = _pdod.get("items", [])
        _emerging = [i["practice"] for i in _pdod_items if i.get("status") == "emerging"]
        if _emerging:
            recs.append(
                (
                    "\u2139 Create a formal Definition of Done",
                    f"The team does some quality checks but inconsistently. "
                    f"{', '.join(_emerging[:3])} are practiced sometimes but not always. "
                    "Write a shared DoD checklist and enforce it on every story.",
                )
            )

    if recs:
        _heading("Recommendations")
        for icon_label, rec_text in recs:
            _add(Text(""))
            t = Text(_PAD + "  ", justify="left")
            t.append(icon_label, style=f"bold {c_warn}")
            _add(t)
            # Wrap recommendation text to fit screen
            max_w = max(40, width - len(_PAD) - 10)
            words = rec_text.split()
            line_buf = ""
            for word in words:
                if len(line_buf) + len(word) + 1 > max_w:
                    r = Text(_PAD + "    ", justify="left")
                    r.append(line_buf.strip(), style=c_muted)
                    _add(r)
                    line_buf = word + " "
                else:
                    line_buf += word + " "
            if line_buf.strip():
                r = Text(_PAD + "    ", justify="left")
                r.append(line_buf.strip(), style=c_muted)
                _add(r)

    # ── Repository Activity ─────────────────────────────────────────
    repos = _ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        top = repos["top_repos"]
        stories_with = repos.get("stories_with_repos", 0)
        _heading("Repository Activity")

        if stories_with:
            sources = repos.get("detection_sources") or []
            if sources:
                src_txt = ", ".join(sources)
                sub = f"  Sources: {src_txt}  ·  {stories_with} stories with repo signals"
            else:
                sub = f"  Repo signals from {stories_with} stories (see ticket text / links)"
            _add(Text(_PAD + sub, style="rgb(80,80,100)", justify="left"))

        # Top repos table
        repo_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 2),
            pad_edge=False,
        )
        repo_table.add_column("Repository", width=28)
        repo_table.add_column("Stories", justify="right", width=8)
        repo_table.add_column("Share", width=12)
        repo_table.add_column("Avg cycle", justify="right", width=10)

        avg_cts = repos.get("repo_avg_cycle_time", {})
        spill_repos_set = {r["repo"] for r in repos.get("spillover_repos", []) if isinstance(r, dict)}

        for r in top[:8]:
            if not isinstance(r, dict):
                continue
            repo_name = r.get("repo", "")
            cnt = r.get("stories", 0)
            pct = r.get("pct", 0)
            avg_ct = avg_cts.get(repo_name)
            bar = _pct_dots(pct, w=10)
            name_style = f"bold {c_warn}" if repo_name in spill_repos_set else c_value
            ct_text = Text(f"{avg_ct:.0f}d" if avg_ct else "—", style=c_warn if avg_ct and avg_ct > 15 else c_muted)
            repo_table.add_row(
                Text(repo_name[:28], style=name_style),
                Text(str(cnt), style=c_muted),
                Text(bar, style=c_accent),
                ct_text,
            )

        _add(Padding(repo_table, (0, 0, 0, len(_PAD) + 2)), rendered_h=repo_table.row_count + 1)

        # Spillover-prone repos
        spill_repos = repos.get("spillover_repos", [])
        if spill_repos:
            _add(Text(""))
            _add(
                Text(
                    _PAD + "  Repos with highest spillover rate:",
                    style=c_muted,
                    justify="left",
                )
            )
            for sr in spill_repos[:3]:
                if not isinstance(sr, dict):
                    continue
                t = Text(_PAD + "    ", justify="left")
                t.append(sr.get("repo", "")[:28], style=f"bold {c_warn}")
                t.append(
                    f"  {sr.get('spill_rate', 0)}% of stories spill ({sr.get('spills', 0)} times)",
                    style=c_muted,
                )
                _add(t)

        # Repos per point value
        by_pts = repos.get("by_pts", {})
        if by_pts:
            _add(Text(""))
            _add(
                Text(
                    _PAD + "  Repos by story size:",
                    style=c_muted,
                    justify="left",
                )
            )
            for pts_key in sorted(by_pts.keys(), key=lambda x: int(x)):
                pt_repos = by_pts[pts_key]
                if not pt_repos:
                    continue
                t = Text(_PAD + "    ", justify="left")
                t.append(f"{pts_key}pt  ", style=c_accent)
                t.append(", ".join(str(r) for r in pt_repos[:3]), style=c_dim)
                _add(t)

    # ── Footer buttons — pinned to the bottom, outside the scroll viewport ──
    # Built separately so they're always visible regardless of scroll position.
    # Button colours: HTML = blue, Markdown = purple, Continue = green.
    # Selected button is bold + bright; unselected is dim.
    _btn_colors = [
        ("rgb(100,140,220)", "bold rgb(100,160,255)"),  # Export HTML — blue
        ("rgb(160,100,220)", "bold rgb(180,120,255)"),  # Export Markdown — purple
        ("rgb(80,180,120)", "bold rgb(80,220,120)"),  # Continue — green
    ]
    btn_line = Text(_PAD, justify="left")
    for i, label in enumerate(["Export HTML", "Export Markdown", "Continue"]):
        _dim_style, _sel_style = _btn_colors[i]
        if i == export_sel:
            btn_line.append(f" [ {label} ] ", style=_sel_style)
        else:
            btn_line.append(f" [ {label} ] ", style=_dim_style)
        if i < 2:
            btn_line.append("  ")
    hint_text = _PAD + "  \u2190 \u2192 select  \u00b7  Enter confirm  \u00b7  \u2191 \u2193 scroll  \u00b7  Esc skip"
    hint_line = Text(hint_text, style="rgb(60,60,80)", justify="left")
    footer_lines = [Text(""), btn_line, hint_line]
    footer_h = len(footer_lines)

    # Scrollable viewport — reserves space for the pinned footer.
    # Use _rendered_lines (not len(lines)) because some items like
    # Padding(RichTable) render as multiple terminal rows.
    inner_h = height - 4
    header_h = 6
    body_h = inner_h - header_h - footer_h

    max_scroll = max(0, _rendered_lines - body_h)
    actual_scroll = min(scroll_offset, max_scroll)
    visible = lines[actual_scroll : actual_scroll + body_h]

    remaining = max(0, body_h - len(visible))

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *visible,
        *[Text("") for _ in range(remaining)],
        *footer_lines,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_intake_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the intake mode selection screen with Planning title pinned at top.

    Shown after the user selects '+ New Project' on the project list.
    Uses the same ASCII art + shimmer + typewriter pattern as the top-level mode screen.
    visible_items: how many intake options to show (-1 = all). For staggered fade-in.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Select intake mode", style="dim", justify="left")

    # Intake option rows — same rendering as mode rows
    show_n = len(_INTAKE_CARDS) if visible_items < 0 else min(visible_items, len(_INTAKE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _INTAKE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_offline_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the offline sub-menu screen with Planning title pinned at top.

    Shown after the user selects 'Offline' on the intake screen.
    Uses the same ASCII art + shimmer + typewriter pattern as the intake mode screen.
    visible_items: how many offline options to show (-1 = all). For staggered reveal.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Offline questionnaire", style="dim", justify="left")

    # Offline option rows — same rendering as mode rows
    show_n = len(_OFFLINE_CARDS) if visible_items < 0 else min(visible_items, len(_OFFLINE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _OFFLINE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the export success screen with Planning title pinned at top.

    Shown after a blank questionnaire template is exported.
    Displays confirmation, file path, and a hint to re-run the agent.
    """
    # Planning title pinned at top
    title = planning_title()

    # Success message body
    body: list = []
    body.append(Text(_PAD + "Questionnaire exported", style="bold bright_green", justify="left"))
    body.append(Text(""))
    body.append(Text(_PAD + f"Saved to: {file_path}", style="white", justify="left"))
    body.append(Text(""))
    body.append(
        Text(
            _PAD + "Fill it in at your own pace, then re-run the agent and select Import.",
            style="dim",
            justify="left",
        )
    )
    body.append(Text(""))
    body.append(Text(_PAD + "Press any key to exit.", style="dim", justify="left"))
    body_h = 7

    # Layout: blank + title(2) + blank + [body]
    inner_h = height - 4
    header_h = 4  # blank + title(2) + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_import_screen(
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    placeholder: str = "scrum-questionnaire.md",
) -> Panel:
    """Build the import file path input screen with Planning title pinned at top.

    Shown when the user selects 'Import' from the offline sub-menu.
    Same text input pattern as provider_select.py API key input.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Import questionnaire", style="dim", justify="left")

    # Input box
    box_w = min(70, width - 16)
    box_inner_w = box_w - 2 - 4  # panel border(2) + padding(4)

    if input_value:
        display = input_value + "\u2588"
        text_style = "bold white"
    else:
        display = placeholder + "\u2588"
        text_style = "rgb(80,80,80)"

    avail = box_inner_w - 4
    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(display) <= avail:
        input_content.append("  " + display, style=text_style)
    else:
        visible = display[-(avail - 1) :]
        input_content.append(" \u25c2", style="dim")
        input_content.append(visible, style=text_style)

    if error:
        border_color = "bright_red"
    else:
        border_color = "white"

    input_box = Panel(
        input_content,
        title=" File path ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )

    # Error text
    error_text = Text(_PAD + error, style="bright_red", justify="left") if error else Text("")

    # Hint
    hint = Text(
        _PAD + "Enter path to a filled .md questionnaire file. Press Enter to confirm.",
        style="dim",
        justify="left",
    )

    body: list = [
        Padding(input_box, (0, 0, 0, len(_PAD))),
        error_text,
        Text(""),
        hint,
    ]
    body_h = 8  # input_box(5) + error(1) + blank + hint(1)

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_project_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
    subtitle: str = "Plan exported",
    hint: str = "Press any key to continue.",
) -> Panel:
    """Build the project export success/status screen.

    Shown after exporting a project's plan as Markdown and HTML,
    or during/after Jira sync operations. subtitle and hint can
    be customised for different contexts (e.g. loading states).
    """
    title = planning_title()

    body: list = [
        Text(_PAD + subtitle, style="bold bright_green", justify="left"),
        Text(""),
    ]
    for line in file_path.splitlines():
        body.append(Text(_PAD + f"  {line}", style="white", justify="left"))
    if hint:
        body.extend(
            [
                Text(""),
                Text(_PAD + hint, style="dim", justify="left"),
            ]
        )
    body_h = 3 + len(file_path.splitlines()) + 2

    inner_h = height - 4
    header_h = 4
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
