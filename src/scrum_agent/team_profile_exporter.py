"""Team profile export — HTML, Markdown, and log reports for team analysis results.

Generates standalone reports from a TeamProfile, reusing the CSS from
html_exporter.py for visual consistency with plan exports.

Exports are sorted into per-project subdirectories under ~/.scrum-agent/exports/:
  ~/.scrum-agent/exports/{project_key}/team-profile-{timestamp}.html
  ~/.scrum-agent/exports/{project_key}/team-profile-{timestamp}.md

Analysis logs are written to ~/.scrum-agent/logs/:
  ~/.scrum-agent/logs/team-analysis-{project_key}-{timestamp}.log
"""

from __future__ import annotations

import html
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scrum_agent.team_profile import TeamProfile

logger = logging.getLogger(__name__)


def _project_export_dir(project_key: str, base_dir: Path | None = None) -> Path:
    """Return the per-project export directory, creating it if needed."""
    base = base_dir or Path.home() / ".scrum-agent" / "exports"
    out_dir = base / project_key.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _format_pct(val: float) -> str:
    """Format a percentage, dropping the decimal if it's .0."""
    return f"{val:.0f}%" if val == int(val) else f"{val:.1f}%"


def _e(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text), quote=True)


def _pct_bar_html(pct: float, width_px: int = 120) -> str:
    """Render a thin percentage bar as inline HTML."""
    fill = min(int(pct), 100)
    color = "#22c55e" if pct >= 80 else ("#eab308" if pct >= 50 else "#ef4444")
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;">'
        f'<span style="display:inline-block;width:{width_px}px;height:6px;'
        f'background:#e2e8f0;border-radius:3px;overflow:hidden;">'
        f'<span style="display:block;width:{fill}%;height:100%;background:{color};'
        f'border-radius:3px;"></span></span>'
        f'<span style="font-size:0.8rem;color:var(--text-muted);">{_format_pct(pct)}</span></span>'
    )


def _section(id_: str, title: str, content: str) -> str:
    """Wrap content in a <section> with id and h2."""
    return f'\n<section id="{id_}"><h2>{_e(title)}</h2>{content}</section>'


def _kv_table(rows: list[tuple[str, str]]) -> str:
    """Render label/value pairs as a two-column card table."""
    trs = "".join(
        f"<tr><td style='width:40%;color:var(--text-muted);'>{_e(lbl)}</td><td style='font-weight:500;'>{v}</td></tr>"
        for lbl, v in rows
    )
    return f'<div class="card" style="padding:0;overflow:hidden;"><table class="data-table">{trs}</table></div>'


def export_team_profile_html(
    profile: TeamProfile,
    output_dir: Path | None = None,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
) -> Path:
    """Generate a self-contained HTML report matching the TUI results screen.

    Returns the path to the generated file.
    """
    from scrum_agent.html_exporter import _CSS

    out_dir = _project_export_dir(profile.project_key, output_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"team-profile-{ts}.html"

    ex = examples or {}
    sections: list[str] = []
    nav_links: list[str] = []

    def _nav(id_: str, label: str) -> None:
        nav_links.append(f'<a href="#{id_}">{_e(label)}</a>')

    # ── Team & Velocity ─────────────────────────────────────────────
    vel_rows: list[tuple[str, str]] = []
    team_sz = ex.get("team_size", 0)
    members = ex.get("team_members", [])
    per_dev = ex.get("per_dev_velocity", 0)

    if team_sz and isinstance(team_sz, int):
        mem_str = f"{team_sz} contributors"
        if members and isinstance(members, list):
            mem_str += f" ({', '.join(str(m) for m in members[:8])})"
        vel_rows.append(("Team size", mem_str))

    # Use sprint_details for accurate velocity if available
    sp_details = ex.get("sprint_details", [])
    if isinstance(sp_details, list) and sp_details:
        import math as _m

        sp_pts = [sd["points"] for sd in sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        vel = round(sum(sp_pts) / len(sp_pts), 1) if sp_pts else profile.velocity_avg
        std = (
            round(_m.sqrt(sum((x - sum(sp_pts) / len(sp_pts)) ** 2 for x in sp_pts) / len(sp_pts)), 1)
            if len(sp_pts) >= 2
            else profile.velocity_stddev
        )
    else:
        vel = profile.velocity_avg
        std = profile.velocity_stddev

    vel_rows.append(("Team velocity", f"{vel} pts/sprint"))
    _html_scope = ex.get("scope_changes", {})
    if isinstance(_html_scope, dict) and _html_scope.get("totals"):
        _hcv = _html_scope["totals"].get("avg_committed_velocity", 0.0)
        _hdv = _html_scope["totals"].get("avg_delivered_velocity", 0.0)
        if _hcv > 0:
            _hdp = round(_hdv / _hcv * 100)
            _hdc = "#22c55e" if _hdp >= 85 else ("#eab308" if _hdp >= 70 else "#ef4444")
            vel_rows.append(("Committed avg", f"{_hcv:g} pts/sprint"))
            vel_rows.append(
                (
                    "Delivered avg",
                    f'{_hdv:g} pts/sprint <span style="color:{_hdc};">({_hdp}% accuracy)</span>',
                )
            )
    if per_dev and isinstance(per_dev, (int, float)) and per_dev > 0:
        vel_rows.append(("Per developer", f"{per_dev} pts/sprint"))
    if vel > 0:
        var_pct = std / vel * 100
        vel_rows.append(("Variance", f"&pm;{std} ({var_pct:.0f}%)"))
    if profile.sprint_completion_rate > 0:
        vel_rows.append(("Completion rate", _pct_bar_html(profile.sprint_completion_rate)))
    if profile.spillover.carried_over_pct > 0:
        vel_rows.append(("Spillover", f"{_format_pct(profile.spillover.carried_over_pct)} carried over"))

    # Velocity trend
    vt = ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend = vt["trend"]
        slope = vt.get("slope", 0)
        first_v = vt.get("first_velocity", 0)
        last_v = vt.get("last_velocity", 0)
        icon = {"improving": "&#x2197;", "degrading": "&#x2198;"}.get(trend, "&#x2192;")
        color = {"improving": "#22c55e", "degrading": "#ef4444"}.get(trend, "var(--text-muted)")
        vel_rows.append(
            (
                "Trend",
                f'<span style="color:{color};font-weight:600;">{icon} {_e(trend.capitalize())}</span>'
                f" ({first_v} &rarr; {last_v}, {slope:+.1f}/sprint)",
            )
        )

    _nav("velocity", "Velocity")
    sections.append(_section("velocity", "Team &amp; Velocity", _kv_table(vel_rows)))

    # ── Recurring work ──────────────────────────────────────────────
    rec_count = ex.get("recurring_count", 0)
    del_count = ex.get("delivery_count", 0)
    rec_items = ex.get("recurring", [])
    if rec_count and isinstance(rec_count, int) and rec_count > 0:
        rec_html = (
            f'<p style="color:var(--text-muted);margin-bottom:0.5rem;">'
            f"{rec_count} recurring tickets excluded "
            f"({del_count} delivery stories analysed)</p>"
        )
        if rec_items and isinstance(rec_items, list):
            rec_lis = "".join(
                f"<li><code>{_e(r.get('issue_key', ''))}</code> {_e(r.get('summary', ''))}</li>"
                for r in rec_items[:5]
                if isinstance(r, dict)
            )
            rec_html += f'<ul style="color:var(--text-muted);font-size:0.85rem;">{rec_lis}</ul>'
        sections.append(f'\n<div class="card" style="border-left:3px solid var(--medium);">{rec_html}</div>')

    # ── Spillover Root Causes ───────────────────────────────────────
    spill_corr = ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_disc = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        has_spill = any(v > 0 for d in (by_size, by_disc, by_tasks) if isinstance(d, dict) for v in d.values())
        if has_spill:
            sc_rows: list[tuple[str, str]] = []
            if by_size:
                sorted_sizes = sorted(by_size.items(), key=lambda x: int(x[0]))
                parts = " &middot; ".join(f"{sz}pt={pct:.0f}%" for sz, pct in sorted_sizes)
                sc_rows.append(("By story size", parts))
            if by_disc:
                parts = " &middot; ".join(f"{d}={pct:.0f}%" for d, pct in sorted(by_disc.items()))
                sc_rows.append(("By discipline", parts))
            if by_tasks:
                parts = " &middot; ".join(f"{b}={pct:.0f}%" for b, pct in by_tasks.items())
                sc_rows.append(("By task count", parts))
            _nav("spillover", "Spillover")
            sections.append(_section("spillover", "Spillover Root Causes", _kv_table(sc_rows)))

    # ── Sprint Breakdown ────────────────────────────────────────────
    if sp_details and isinstance(sp_details, list) and len(sp_details) > 0:
        sp_hdr = "<tr><th>Sprint</th><th>Pts</th><th>Done</th><th>Rate</th><th></th></tr>"
        sp_rows_html = []
        for sd in sp_details:
            if not isinstance(sd, dict):
                continue
            name = _e(sd.get("name", "?"))
            pts = sd.get("points", 0)
            planned = sd.get("planned", 0)
            completed = sd.get("completed", 0)
            rate = sd.get("rate", 0)
            done = sd.get("done", False)
            has_shadow = sd.get("has_shadow", False)
            icon = "&#x2713;" if done else ("&#x25cb;" if has_shadow else "&#x2717;")
            icon_color = "#22c55e" if done else ("#eab308" if has_shadow else "#ef4444")
            rate_color = "#22c55e" if rate >= 80 else ("#eab308" if rate >= 50 else "#ef4444")
            sp_rows_html.append(
                f"<tr><td>{name}</td><td>{pts}</td><td>{completed}/{planned}</td>"
                f'<td style="color:{rate_color};font-weight:600;">{rate}%</td>'
                f'<td style="color:{icon_color};">{icon}</td></tr>'
            )
        if sp_rows_html:
            sprint_content = (
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{sp_hdr}{"".join(sp_rows_html)}</table></div>'
            )

            # Incomplete sprint analysis
            incomplete = [
                sd
                for sd in sp_details
                if isinstance(sd, dict)
                and (not sd.get("done", False) or sd.get("has_shadow", False))
                and sd.get("incomplete")
            ]
            if incomplete:
                sprint_content += (
                    '<h3 style="font-size:0.9rem;color:var(--text-muted);'
                    'margin-top:1rem;">Incomplete sprint analysis</h3>'
                )
                for sd in incomplete[:3]:
                    sname = _e(sd.get("name", "?"))
                    gap = sd.get("planned", 0) - sd.get("completed", 0)
                    has_sh = sd.get("has_shadow", False)
                    label_parts = []
                    if gap > 0:
                        label_parts.append(f"{gap} stories not completed")
                    if has_sh:
                        label_parts.append("shadow spillover")
                    sprint_content += (
                        f'<div class="card" style="border-left:3px solid #eab308;margin:0.5rem 0;">'
                        f'<strong style="color:#eab308;">{sname}</strong>'
                        f'<span style="color:var(--text-muted);margin-left:0.5rem;">{" + ".join(label_parts)}</span>'
                    )
                    for item in sd.get("incomplete", [])[:3]:
                        if not isinstance(item, dict):
                            continue
                        ek = _e(item.get("issue_key", ""))
                        sm = _e(item.get("summary", ""))
                        shadow = item.get("shadow", False)
                        pts_v = item.get("points", 0)
                        detail = " (re-created)" if shadow else (f" ({pts_v}pts)" if pts_v else "")
                        sprint_content += (
                            f'<div style="margin-left:1rem;font-size:0.85rem;color:var(--text-muted);">'
                            f"<code>{ek}</code> {sm}"
                            f'<span style="color:#eab308;">{detail}</span></div>'
                        )
                    sprint_content += "</div>"

            # Append scope tracking into sprint breakdown section
            _sc_scope = ex.get("scope_changes", {})
            if isinstance(_sc_scope, dict) and _sc_scope.get("totals"):
                _sc_t = _sc_scope["totals"]
                _sc_a = _sc_t.get("added_mid_sprint", 0)
                _sc_r = _sc_t.get("re_estimated", 0)
                _sc_n = _sc_t.get("total_stories", 0)
                _sc_cv = _sc_t.get("avg_committed_velocity", 0.0)
                _sc_dv = _sc_t.get("avg_delivered_velocity", 0.0)
                if _sc_a > 0 or _sc_r > 0 or _sc_cv > 0:
                    sprint_content += '<hr style="border:none;border-top:1px solid var(--border);margin:1rem 0;">'
                    if _sc_cv > 0:
                        _dp = round(_sc_dv / _sc_cv * 100)
                        _dc = "#22c55e" if _dp >= 85 else ("#eab308" if _dp >= 70 else "#ef4444")
                        sprint_content += (
                            f"<p>Committed <strong>{_sc_cv:g}</strong> &rarr; "
                            f"Delivered <strong>{_sc_dv:g}</strong> pts/sprint avg "
                            f'<span style="color:{_dc};">({_dp}% accuracy)</span></p>'
                        )
                    if _sc_n > 0 and (_sc_a > 0 or _sc_r > 0):
                        sprint_content += (
                            f'<p style="font-size:0.85rem;">{_sc_a} added mid-sprint '
                            f"({_sc_a * 100 // _sc_n}%) &middot; "
                            f"{_sc_r} re-estimated ({_sc_r * 100 // _sc_n}%)</p>"
                        )
                    _sc_tls = _sc_scope.get("timelines", [])
                    _sc_we = [t for t in _sc_tls if hasattr(t, "change_events") and t.change_events]
                    for tl in _sc_we[-4:]:
                        _d = tl.scope_change_total
                        _p = round(_d / tl.committed_pts * 100) if tl.committed_pts else 0
                        _ds = f"+{_d:g}" if _d > 0 else f"{_d:g}"
                        _dcol = "#22c55e" if _d == 0 else ("#eab308" if abs(_d) < 5 else "#ef4444")
                        _ns = len(tl.daily_snapshots[0].stories_in_sprint) if tl.daily_snapshots else 0
                        _nf = len(tl.daily_snapshots[-1].stories_in_sprint) if tl.daily_snapshots else 0
                        sprint_content += (
                            f'<div style="margin:1rem 0 0.5rem 0;padding:0.5rem;'
                            f'border-left:3px solid {_dcol};background:rgba(255,255,255,0.02);">'
                            f"<strong>{_e(tl.sprint_name)}</strong> "
                            f'<span style="color:{_dcol};">{_ds} scope ({_p:+d}%)</span>'
                            f'<div style="font-size:0.85rem;color:var(--text-muted);margin:0.25rem 0;">'
                            f"committed {tl.committed_pts:g} pts ({_ns} stories)</div>"
                        )
                        for ev in tl.change_events[:5]:
                            ct = ev.change_type.replace("re_estimated_", "re-est ").replace("_", " ")
                            evd = f"+{ev.delta_pts:g}" if ev.delta_pts > 0 else f"{ev.delta_pts:g}"
                            evc = (
                                "#22c55e" if ev.delta_pts < 0 else ("#eab308" if abs(ev.delta_pts) <= 3 else "#ef4444")
                            )
                            sprint_content += (
                                f'<div style="font-size:0.85rem;margin:0.1rem 0 0 1rem;">'
                                f'<span style="color:{evc};">{evd} pts</span> '
                                f"<code>{_e(ev.issue_key)}</code> {_e(ct)}"
                            )
                            if ev.summary:
                                sprint_content += (
                                    f' <span style="color:var(--text-muted);">{_e(ev.summary[:45])}</span>'
                                )
                            sprint_content += "</div>"
                        if len(tl.change_events) > 5:
                            sprint_content += (
                                f'<div style="font-size:0.8rem;margin-left:1rem;color:var(--text-muted);">'
                                f"... +{len(tl.change_events) - 5} more</div>"
                            )
                        sprint_content += (
                            f'<div style="font-size:0.85rem;color:var(--text-muted);margin:0.25rem 0;">'
                            f"final {tl.final_pts:g} pts ({_nf} stories) &middot; "
                            f"delivered {tl.delivered_pts:g} pts</div></div>"
                        )
                    _sc_chains = _sc_scope.get("carry_over_chains", [])
                    if _sc_chains:
                        sprint_content += (
                            f'<h3 style="font-size:0.85rem;color:#eab308;margin-top:0.75rem;">'
                            f"{len(_sc_chains)} stories bounced across 3+ sprints</h3>"
                        )
                        for ch in _sc_chains[:5]:
                            if isinstance(ch, dict):
                                ek = _e(ch.get("issue_key", ""))
                                sps = " &rarr; ".join(_e(str(s)) for s in ch.get("sprints", []))
                                sprint_content += (
                                    f'<div style="margin:0.2rem 0 0 1rem;font-size:0.85rem;">'
                                    f"<code>{ek}</code> {sps}</div>"
                                )

            _nav("sprints", "Sprints")
            sections.append(_section("sprints", "Sprint Breakdown", sprint_content))

    # ── Shadow Spillover ────────────────────────────────────────────
    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        shadow_html = (
            f'<div class="card" style="border-left:3px solid #eab308;">'
            f'<strong style="color:#eab308;">&#x26a0; {len(shadow)} re-created stories detected</strong>'
            f'<p style="color:var(--text-muted);">Closed in one sprint but re-created in the next:</p>'
        )
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            ek = _e(sh.get("issue_key", ""))
            url = sh.get("issue_url", "")
            title = _e(sh.get("title", ""))
            from_sp = _e(sh.get("from_sprint", ""))
            to_sp = _e(sh.get("to_sprint", ""))
            key_html = f'<a href="{_e(url)}"><code>{ek}</code></a>' if url else f"<code>{ek}</code>"
            shadow_html += (
                f'<div style="margin:0.3rem 0 0 1rem;font-size:0.85rem;">'
                f"{key_html} {title}"
                f'<span style="color:var(--text-muted);margin-left:0.5rem;">{from_sp} &rarr; {to_sp}</span>'
                f"</div>"
            )
        shadow_html += "</div>"
        sections.append(f"\n{shadow_html}")

    # ── Discipline-Specific Calibration ─────────────────────────────
    disc_cal = ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        disc_content = ""
        for disc, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            disc_hdr = "<tr><th>Points</th><th>Cycle time</th><th>Variance</th><th>Samples</th><th>Spillover</th></tr>"
            disc_rows = ""
            for e in entries:
                if not isinstance(e, dict):
                    continue
                pts = e.get("points", 0)
                avg_d = e.get("avg_cycle_days", 0)
                var = e.get("variance", 0)
                samples = e.get("samples", 0)
                sp = e.get("spill_pct", 0)
                var_html = f"&pm;{var:.0f}d" if var > 0 else "&mdash;"
                sp_color = "#22c55e" if sp < 10 else ("#eab308" if sp < 25 else "#ef4444")
                sp_html = f'<span style="color:{sp_color};">{sp:.0f}%</span>' if sp > 0 else "&mdash;"
                disc_rows += (
                    f"<tr><td>{pts}pt{'s' if pts != 1 else ''}</td>"
                    f"<td>{avg_d:.0f}d</td><td>{var_html}</td>"
                    f"<td>{samples}</td><td>{sp_html}</td></tr>"
                )
            disc_content += (
                f'<h3 style="font-size:0.9rem;margin-top:1rem;">{_e(disc)}</h3>'
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{disc_hdr}{disc_rows}</table></div>'
            )
        _nav("disc-cal", "Discipline Cal.")
        sections.append(_section("disc-cal", "Calibration by Discipline", disc_content))

    # ── Point Calibration ───────────────────────────────────────────
    cals = [c for c in profile.point_calibrations if c.sample_count > 0]
    conf_levels = ex.get("confidence_levels", {})
    if cals:
        cal_hdr = (
            "<tr><th>Points</th><th>Avg cycle time</th><th>Samples</th>"
            "<th>Tasks</th><th>Slip</th><th>Confidence</th></tr>"
        )
        cal_rows_html = []
        for c in cals:
            conf = conf_levels.get(c.point_value, "") if isinstance(conf_levels, dict) else ""
            conf_color = {"high": "#22c55e", "medium": "var(--text-muted)", "low": "#eab308"}.get(conf, "")
            conf_html = f'<span style="color:{conf_color};font-weight:600;">{conf.upper()}</span>' if conf else ""
            cal_rows_html.append(
                f"<tr><td><strong>{c.point_value} pt{'s' if c.point_value != 1 else ''}</strong></td>"
                f"<td>{c.avg_cycle_time_days:.0f} days</td>"
                f"<td>{c.sample_count}</td>"
                f"<td>~{c.typical_task_count:.0f}</td>"
                f"<td>{_format_pct(c.overshoot_pct)}</td>"
                f"<td>{conf_html}</td></tr>"
            )
            if c.common_patterns:
                pats = ", ".join(_e(p) for p in c.common_patterns)
                cal_rows_html.append(
                    f'<tr><td colspan="6" style="color:var(--text-muted);font-size:0.8rem;'
                    f'padding-left:2rem;">Typical: {pats}</td></tr>'
                )
            # Issue key examples
            cal_examples = ex.get(f"calibration_{c.point_value}pt", [])
            for ce in cal_examples[:2]:
                if not isinstance(ce, dict):
                    continue
                ek = _e(ce.get("issue_key", ""))
                url = ce.get("issue_url", "")
                sm = _e(ce.get("summary", ""))
                detail = _e(ce.get("detail", ""))
                key_html = f'<a href="{_e(url)}"><code>{ek}</code></a>' if url else f"<code>{ek}</code>"
                cal_rows_html.append(
                    f'<tr><td colspan="6" style="font-size:0.8rem;padding-left:2rem;">'
                    f'{key_html} <span style="color:var(--text-muted);">{sm}</span>'
                    f"{f' <em>{detail}</em>' if detail else ''}</td></tr>"
                )
        cal_table = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{cal_hdr}{"".join(cal_rows_html)}</table></div>'
        )
        _nav("calibration", "Calibration")
        sections.append(_section("calibration", "What Each Point Value Means", cal_table))

    # ── Story Shapes ────────────────────────────────────────────────
    shapes = [s for s in profile.story_shapes if s.sample_count > 0]
    if shapes:
        sh_hdr = "<tr><th>Discipline</th><th>Avg pts</th><th>Avg ACs</th><th>Avg tasks</th><th>Samples</th></tr>"
        sh_rows = "".join(
            f"<tr><td><strong>{_e(s.discipline)}</strong></td><td>{s.avg_points}</td>"
            f"<td>{s.avg_ac_count}</td><td>{s.avg_task_count}</td><td>{s.sample_count}</td></tr>"
            for s in shapes
        )
        shape_table = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{sh_hdr}{sh_rows}</table></div>'
        )
        _nav("shapes", "Story Shapes")
        sections.append(_section("shapes", "Story Shape by Discipline", shape_table))

    # ── Task Decomposition ──────────────────────────────────────────
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_tasks", 0) > 0:
        td_rows: list[tuple[str, str]] = [
            ("Stories with tasks", f"{td['stories_with_tasks']} / {td['total_stories']}"),
            ("Total tasks", str(td["total_tasks"])),
            ("Avg tasks/story", str(td["avg_tasks_per_story"])),
            ("Task completion", _pct_bar_html(td["task_completion_rate"])),
        ]
        td_content = _kv_table(td_rows)

        type_dist = td.get("type_distribution", {})
        if type_dist:
            dist_rows = "".join(
                f"<tr><td>{_e(cat)}</td><td>{_pct_bar_html(pct)}</td></tr>" for cat, pct in type_dist.items()
            )
            td_content += (
                f'<div class="card" style="padding:0;overflow:hidden;margin-top:0.5rem;">'
                f'<table class="data-table">{dist_rows}</table></div>'
            )

        # Bottlenecks
        bottlenecks = td.get("bottlenecks", [])
        for cat, rate_val, count in bottlenecks:
            td_content += (
                f'<div class="card" style="border-left:3px solid #eab308;margin-top:0.5rem;">'
                f'<strong style="color:#eab308;">&#x26a0; {_e(str(cat))} bottleneck</strong>'
                f'<p style="color:var(--text-muted);">'
                f"Only {rate_val}% completion ({count} tasks)</p></div>"
            )

        # Common task patterns
        common_tasks = td.get("common_tasks", [])
        if common_tasks:
            ct_rows = "".join(
                f"<tr><td>{_e(str(title)[:45])}</td><td>&times;{cnt}</td></tr>" for title, cnt in common_tasks[:4]
            )
            td_content += (
                f'<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">'
                f"Common task patterns</h3>"
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{ct_rows}</table></div>'
            )

        # Task assignees
        assignees = td.get("task_assignees", {})
        if assignees:
            ta_rows = "".join(
                f"<tr><td>{_e(str(name))}</td><td>{cnt} tasks</td></tr>" for name, cnt in list(assignees.items())[:5]
            )
            td_content += (
                f'<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">'
                f"Task assignees</h3>"
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{ta_rows}</table></div>'
            )

        _nav("tasks", "Tasks")
        sections.append(_section("tasks", "Task Decomposition", td_content))

    # ── DoD Signals ─────────────────────────────────────────────────
    dod = profile.dod_signal
    dod_items_with_key: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_items_with_key.append(("Testing mentioned", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_items_with_key.append(("PR linked before close", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_items_with_key.append(("Code review mentioned", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_items_with_key.append(("Deploy mentioned", dod.stories_with_deploy_mention_pct, "dod_deploy"))

    if dod_items_with_key:
        dod_hdr = "<tr><th>Practice</th><th>Coverage</th><th>Example</th></tr>"
        dod_rows_html = ""
        for label, pct, ekey in dod_items_with_key:
            ex_items = ex.get(ekey, [])
            ex_html = ""
            if ex_items and isinstance(ex_items, list) and ex_items:
                e0 = ex_items[0]
                if isinstance(e0, dict):
                    ek = _e(e0.get("issue_key", ""))
                    eu = e0.get("issue_url", "")
                    sm = _e(e0.get("summary", "")[:30])
                    key_h = f'<a href="{_e(eu)}"><code>{ek}</code></a>' if eu else f"<code>{ek}</code>"
                    ex_html = f'{key_h} <span style="color:var(--text-muted);">{sm}</span>'
            dod_rows_html += (
                f"<tr><td>{_e(label)}</td><td>{_pct_bar_html(pct)}</td>"
                f'<td style="font-size:0.8rem;">{ex_html}</td></tr>'
            )
        if dod.common_checklist_items:
            items = ", ".join(_e(i) for i in dod.common_checklist_items[:6])
            dod_rows_html += (
                f'<tr><td>Common signals</td><td colspan="2" style="color:var(--text-muted);">{items}</td></tr>'
            )
        dod_table = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{dod_hdr}{dod_rows_html}</table></div>'
        )
        _nav("dod", "DoD")
        sections.append(_section("dod", "Definition of Done (inferred)", dod_table))

    # ── Proposed DoD ───────────────────────────────────────────────
    pdod = ex.get("proposed_dod", {})
    if isinstance(pdod, dict) and pdod.get("items"):
        pdod_summary = pdod.get("summary", "")
        pdod_health = pdod.get("health", "weak")
        h_col = "#22c55e" if pdod_health == "strong" else ("#eab308" if pdod_health == "moderate" else "#ef4444")
        pdod_html = f'<p style="color:{h_col};font-weight:bold;">{_e(pdod_summary)}</p>'
        pdod_html += (
            '<table class="data-table"><tr><th>Practice</th><th>Status</th><th>Evidence</th><th>Action</th></tr>'
        )
        _pst_icon = {"established": "&#x2713;", "emerging": "&#x25cb;", "missing": "&#x2717;"}
        _pst_col = {"established": "#22c55e", "emerging": "#eab308", "missing": "#ef4444"}
        for item in pdod["items"]:
            st = item.get("status", "missing")
            sig = item.get("signals", "no evidence")
            pdod_html += (
                f"<tr><td>{_e(item.get('practice', ''))}</td>"
                f'<td style="color:{_pst_col.get(st, "#888")};">'
                f"{_pst_icon.get(st, '?')} {_e(st)}</td>"
                f'<td style="color:var(--text-muted);">{_e(sig)}</td>'
                f'<td style="color:var(--text-muted);font-size:0.85rem;">'
                f"{_e(item.get('recommendation', ''))}</td></tr>"
            )
        pdod_html += "</table>"
        dod_ordering = pdod.get("ordering", [])
        if len(dod_ordering) >= 2:
            pdod_html += (
                f'<p style="margin-top:0.5rem;color:var(--text-muted);">'
                f"Typical order: {' &rarr; '.join(_e(o) for o in dod_ordering)}</p>"
            )
        custom_steps = pdod.get("custom_steps", [])
        if custom_steps:
            parts = ", ".join(f"&ldquo;{_e(cs['title'])}&rdquo; ({cs['pct']}%)" for cs in custom_steps[:4])
            pdod_html += f'<p style="color:var(--text-muted);">Team-specific steps: {parts}</p>'
        _nav("proposed-dod", "Proposed DoD")
        sections.append(_section("proposed-dod", "Proposed Definition of Done", pdod_html))

    # ── Writing Patterns ────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_rows: list[tuple[str, str]] = []
    if wp.uses_given_when_then:
        wp_rows.append(("AC format", "Given/When/Then &#x2713;"))
    if wp.median_ac_count > 0:
        wp_rows.append(("Median ACs/story", str(wp.median_ac_count)))
    if wp.median_task_count_per_story > 0:
        wp_rows.append(("Median tasks/story", str(wp.median_task_count_per_story)))
    if wp.subtask_label_distribution:
        parts = " &middot; ".join(f"{_e(lbl)} {int(pct * 100)}%" for lbl, pct in wp.subtask_label_distribution[:5])
        wp_rows.append(("Sub-task types", parts))
    if wp.common_personas:
        wp_rows.append(("Personas", _e(", ".join(wp.common_personas[:5]))))
    if wp_rows:
        _nav("patterns", "Patterns")
        sections.append(_section("patterns", "Writing Patterns", _kv_table(wp_rows)))

    # ── Repository Activity ─────────────────────────────────────────
    repos = ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        top = repos["top_repos"]
        avg_cts = repos.get("repo_avg_cycle_time", {})
        spill_repos_set = {r["repo"] for r in repos.get("spillover_repos", []) if isinstance(r, dict)}

        repo_hdr = "<tr><th>Repository</th><th>Stories</th><th>Share</th><th>Avg cycle</th></tr>"
        repo_rows_html = ""
        for r in top[:8]:
            if not isinstance(r, dict):
                continue
            rname = r.get("repo", "")
            cnt = r.get("stories", 0)
            pct = r.get("pct", 0)
            avg_ct = avg_cts.get(rname) if isinstance(avg_cts, dict) else None
            ct_html = f"{avg_ct:.0f}d" if avg_ct else "&mdash;"
            ct_color = "#eab308" if avg_ct and avg_ct > 15 else "var(--text-muted)"
            name_style = "color:#eab308;font-weight:600;" if rname in spill_repos_set else ""
            repo_rows_html += (
                f'<tr><td style="{name_style}"><strong>{_e(rname)}</strong></td>'
                f"<td>{cnt}</td><td>{_pct_bar_html(pct, 80)}</td>"
                f'<td style="color:{ct_color};">{ct_html}</td></tr>'
            )

        repo_content = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{repo_hdr}{repo_rows_html}</table></div>'
        )

        # Spillover-prone repos
        spill_repos = repos.get("spillover_repos", [])
        if spill_repos and isinstance(spill_repos, list):
            repo_content += (
                '<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">'
                "Repos with highest spillover rate</h3>"
            )
            for sr in spill_repos[:3]:
                if not isinstance(sr, dict):
                    continue
                repo_content += (
                    f'<div style="margin:0.3rem 0 0 1rem;font-size:0.85rem;">'
                    f'<strong style="color:#eab308;">{_e(sr.get("repo", ""))}</strong>'
                    f' <span style="color:var(--text-muted);">'
                    f"{sr.get('spill_rate', 0)}% spillover ({sr.get('spills', 0)} times)</span></div>"
                )

        # Repos by point value
        by_pts = repos.get("by_pts", {})
        if by_pts and isinstance(by_pts, dict):
            repo_content += (
                '<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">Repos by story size</h3>'
            )
            for pts_key in sorted(by_pts.keys(), key=lambda x: int(x)):
                pt_repos = by_pts[pts_key]
                if not pt_repos:
                    continue
                repo_content += (
                    f'<div style="margin:0.2rem 0 0 1rem;font-size:0.85rem;">'
                    f"<strong>{pts_key}pt</strong>"
                    f' <span style="color:var(--text-muted);">'
                    f"{', '.join(_e(str(r)) for r in pt_repos[:3])}</span></div>"
                )

        _nav("repos", "Repos")
        sections.append(_section("repos", "Repository Activity", repo_content))

    # ── Epic Sizing ─────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        lo, hi = epic.typical_story_count_range
        ep_rows: list[tuple[str, str]] = [
            ("Avg stories/epic", f"{epic.avg_stories_per_epic:.0f}"),
            ("Avg points/epic", f"{epic.avg_points_per_epic:.0f}"),
        ]
        if lo > 0 or hi > 0:
            ep_rows.append(("Story count range", f"{lo}&ndash;{hi}"))
        sections.append(_section("epics", "Epic Sizing", _kv_table(ep_rows)))

    # ── Recommendations (all 13 types, matching TUI) ────────────────
    recs: list[tuple[str, str]] = []
    if vel > 0:
        var_pct = std / vel * 100
        if var_pct > 35:
            recs.append(
                (
                    "High velocity variance",
                    f"Velocity swings &pm;{var_pct:.0f}% sprint-to-sprint. "
                    "Consider smaller stories or stricter sprint commitments.",
                )
            )
    if profile.sprint_completion_rate > 0 and profile.sprint_completion_rate < 60:
        recs.append(
            (
                "Low sprint completion",
                f"Only {profile.sprint_completion_rate:.0f}% of planned work completes. "
                "Right-size commitments to 80-90% of velocity.",
            )
        )
    if profile.spillover.carried_over_pct > 15:
        recs.append(
            (
                "Frequent spillover",
                f"{profile.spillover.carried_over_pct:.0f}% of stories carry over. "
                "Break large stories into smaller slices.",
            )
        )
    for c in cals:
        if c.point_value >= 8 and c.avg_cycle_time_days > 60:
            recs.append(
                (
                    f"{c.point_value}-point stories too large",
                    f"{c.point_value}-point stories take {c.avg_cycle_time_days:.0f}d on average. "
                    "Consider splitting into smaller pieces.",
                )
            )
            break
    dod = profile.dod_signal
    if 0 < dod.stories_with_testing_mention_pct < 15:
        recs.append(
            (
                "Testing rarely mentioned",
                f"Only {dod.stories_with_testing_mention_pct:.0f}% of stories mention testing. "
                "Add explicit test criteria to acceptance criteria.",
            )
        )
    if 0 < dod.stories_with_pr_link_pct < 20:
        recs.append(
            (
                "Low PR linkage",
                f"Only {dod.stories_with_pr_link_pct:.0f}% of stories reference a PR. "
                "Link PRs to tickets for traceability.",
            )
        )
    rec_count_val = ex.get("recurring_count", 0)
    del_count_val = ex.get("delivery_count", 0)
    if isinstance(rec_count_val, int) and isinstance(del_count_val, int):
        total = rec_count_val + del_count_val
        if total > 0 and rec_count_val / total > 0.3:
            recs.append(
                (
                    "High recurring overhead",
                    f"{rec_count_val} of {total} tickets ({rec_count_val / total * 100:.0f}%) "
                    "are recurring. Consider consolidating or timeboxing.",
                )
            )
    per_dev = ex.get("per_dev_velocity", 0)
    team_sz = ex.get("team_size", 0)
    if team_sz and isinstance(team_sz, int) and per_dev and isinstance(per_dev, (int, float)) and per_dev < 3:
        recs.append(
            (
                "Low per-developer output",
                f"Each developer averages {per_dev} pts/sprint. "
                "Check for blockers, context-switching, or oversized stories.",
            )
        )
    _repos = ex.get("repositories", {})
    if isinstance(_repos, dict):
        for sr in _repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append(
                    (
                        f"{_e(sr['repo'])} has high spillover",
                        f"{sr['spill_rate']}% of stories touching {_e(sr['repo'])} don't complete the sprint.",
                    )
                )
    _shadow = ex.get("shadow_spillover", [])
    if isinstance(_shadow, list) and len(_shadow) >= 2:
        recs.append(
            (
                "Shadow spillover",
                f"{len(_shadow)} stories were closed then re-created in the next sprint. "
                "Consider keeping the original ticket open instead of cloning.",
            )
        )
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict):
        if td.get("task_completion_rate", 100) < 60:
            recs.append(
                (
                    "Low task completion",
                    f"Only {td['task_completion_rate']}% of sub-tasks are completed.",
                )
            )
        for cat, rate_val, count in td.get("bottlenecks", []):
            recs.append(
                (
                    f"{cat} bottleneck",
                    f"{cat} tasks have only {rate_val}% completion ({count} tasks).",
                )
            )
        sw = td.get("stories_with_tasks", 0)
        tot = td.get("total_stories", 0)
        if tot > 10 and sw > 0 and sw / tot < 0.3:
            recs.append(
                (
                    "Low task breakdown",
                    f"Only {sw} of {tot} stories ({sw / tot * 100:.0f}%) have sub-tasks.",
                )
            )

    # Scope change recommendations
    _sc = ex.get("scope_changes", {})
    if isinstance(_sc, dict) and _sc.get("totals"):
        _sct = _sc["totals"]
        _sct_n = _sct.get("total_stories", 0)
        _sct_cv = _sct.get("avg_committed_velocity", 0.0)
        _sct_dv = _sct.get("avg_delivered_velocity", 0.0)
        if _sct_cv > 0 and _sct_dv / _sct_cv < 0.7:
            _dp = round(_sct_dv / _sct_cv * 100)
            recs.append(
                (
                    "Low delivery accuracy",
                    f"Team delivers only {_dp}% of committed scope "
                    f"({_sct_dv} of {_sct_cv} pts avg). "
                    "Reduce sprint commitments to match actual capacity.",
                )
            )
        if _sct_n > 0:
            _sct_a = _sct.get("added_mid_sprint", 0)
            _sct_r = _sct.get("re_estimated", 0)
            if _sct_a / _sct_n > 0.15:
                recs.append(
                    (
                        "High mid-sprint scope additions",
                        f"{_sct_a} of {_sct_n} stories ({_sct_a / _sct_n * 100:.0f}%) "
                        "were added after the sprint started. "
                        "Protect sprint commitments by locking scope after planning.",
                    )
                )
            if _sct_r / _sct_n > 0.15:
                recs.append(
                    (
                        "Frequent re-estimation",
                        f"{_sct_r} of {_sct_n} stories ({_sct_r / _sct_n * 100:.0f}%) "
                        "had their points changed mid-sprint. "
                        "Improve estimation accuracy with team calibration sessions.",
                    )
                )
        _sc_sps = _sc.get("per_sprint", [])
        _hi_churn = [s for s in _sc_sps if s.get("scope_churn", 0) > 0.3]
        if len(_hi_churn) >= 2:
            _cn = ", ".join(s.get("name", "?") for s in _hi_churn[:3])
            recs.append(
                (
                    "High scope churn",
                    f"{len(_hi_churn)} sprints had &gt;30% scope churn ({_e(_cn)}). "
                    "Scope is volatile &mdash; enforce a sprint lock after planning.",
                )
            )
        _sc_ch = _sc.get("carry_over_chains", [])
        if len(_sc_ch) >= 3:
            recs.append(
                (
                    "Carry-over chains",
                    f"{len(_sc_ch)} stories bounced across 3+ sprints. "
                    "These are zombie stories &mdash; split or kill them.",
                )
            )

    _html_pdod = ex.get("proposed_dod", {})
    if isinstance(_html_pdod, dict) and _html_pdod.get("health") == "weak":
        _hm = [i["practice"] for i in _html_pdod.get("items", []) if i.get("status") == "missing"]
        recs.append(
            (
                "No consistent Definition of Done",
                f"No consistent DoD found. {_e(', '.join(_hm[:3]))} show no evidence. "
                "Create a team DoD checklist to improve quality.",
            )
        )
    elif isinstance(_html_pdod, dict) and _html_pdod.get("health") == "moderate":
        _he = [i["practice"] for i in _html_pdod.get("items", []) if i.get("status") == "emerging"]
        if _he:
            recs.append(
                (
                    "Create a formal Definition of Done",
                    f"{_e(', '.join(_he[:3]))} are practiced inconsistently. "
                    "Write a shared DoD checklist and enforce it on every story.",
                )
            )

    if recs:
        rec_html_items = "".join(
            f'<div class="card" style="border-left:3px solid #eab308;margin-bottom:0.5rem;">'
            f'<strong style="color:#eab308;">&#x26a0; {title}</strong>'
            f'<p style="color:var(--text-muted);margin-top:0.3rem;">{desc}</p></div>'
            for title, desc in recs
        )
        _nav("recommendations", "Recs")
        sections.append(_section("recommendations", "Recommendations", rec_html_items))

    # ── Assemble page ───────────────────────────────────────────────
    esc_key = _e(profile.project_key)
    esc_src = _e(profile.source)
    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    nav_html = ""
    if nav_links:
        nav_html = f'<nav class="toc">{"".join(nav_links)}</nav>'

    sprint_names_html = ""
    if sprint_names:
        sprint_names_html = (
            f'<span class="badge" style="background:rgba(255,255,255,0.15);padding:0.1rem 0.6rem;'
            f'border-radius:999px;font-size:0.78rem;">'
            f"{', '.join(_e(n) for n in sprint_names)}</span>"
        )

    body_content = f'<div class="container">{"".join(sections)}</div>'

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Team Profile &mdash; {esc_key}</title>
<style>{_CSS}</style>
</head>
<body>
<header class="site-header">
  <h1>Team Profile &mdash; {esc_src}/{esc_key}</h1>
  <div class="meta">
    <span>{profile.sample_sprints} sprints analysed</span>
    <span>{profile.sample_stories} stories</span>
    <span>Generated {_e(gen_ts)}</span>
    {sprint_names_html}
  </div>
</header>
{nav_html}
{body_content}
<footer class="site-footer">
  Generated by Scrum AI Agent &bull; {_e(datetime.now().strftime("%Y-%m-%d"))}
</footer>
</body>
</html>"""

    out_path.write_text(page, encoding="utf-8")
    logger.info("Exported team profile HTML to %s", out_path)
    return out_path


def export_team_profile_md(
    profile: TeamProfile,
    output_dir: Path | None = None,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
) -> Path:
    """Generate a Markdown report matching the TUI results screen.

    Returns the path to the generated file.
    """
    out_dir = _project_export_dir(profile.project_key, output_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"team-profile-{ts}.md"

    ex = examples or {}
    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"# Team Profile — {profile.source}/{profile.project_key}",
        "",
        f"*{profile.sample_sprints} sprints · {profile.sample_stories} stories · Generated {gen_ts}*",
    ]
    if sprint_names:
        lines.append(f"\nSprints: {', '.join(sprint_names)}")
    lines.append("")

    # ── Recurring work ──────────────────────────────────────────────
    rec_count = ex.get("recurring_count", 0)
    del_count = ex.get("delivery_count", 0)
    rec_items = ex.get("recurring", [])
    if rec_count and isinstance(rec_count, int) and rec_count > 0:
        lines.append(f"> {rec_count} recurring tickets excluded ({del_count} delivery stories analysed)")
        if rec_items and isinstance(rec_items, list):
            for r in rec_items[:5]:
                if isinstance(r, dict):
                    lines.append(f">   - `{r.get('issue_key', '')}` {r.get('summary', '')}")
        lines.append("")

    # ── Team & Velocity ─────────────────────────────────────────────
    lines.extend(["## Team & Velocity", ""])
    team_sz = ex.get("team_size", 0)
    members = ex.get("team_members", [])
    per_dev = ex.get("per_dev_velocity", 0)
    if team_sz and isinstance(team_sz, int):
        mem = f" ({', '.join(str(m) for m in members[:8])})" if members else ""
        lines.append(f"- **Team size:** {team_sz} contributors{mem}")

    sp_details = ex.get("sprint_details", [])
    if isinstance(sp_details, list) and sp_details:
        import math as _m

        sp_pts = [sd["points"] for sd in sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        vel = round(sum(sp_pts) / len(sp_pts), 1) if sp_pts else profile.velocity_avg
        std = (
            round(_m.sqrt(sum((x - sum(sp_pts) / len(sp_pts)) ** 2 for x in sp_pts) / len(sp_pts)), 1)
            if len(sp_pts) >= 2
            else profile.velocity_stddev
        )
    else:
        vel = profile.velocity_avg
        std = profile.velocity_stddev

    lines.append(f"- **Velocity:** {vel} pts/sprint")
    _md_vsc = ex.get("scope_changes", {})
    if isinstance(_md_vsc, dict) and _md_vsc.get("totals"):
        _mcv = _md_vsc["totals"].get("avg_committed_velocity", 0.0)
        _mdv = _md_vsc["totals"].get("avg_delivered_velocity", 0.0)
        if _mcv > 0:
            _mdp = round(_mdv / _mcv * 100)
            lines.append(f"- **Committed avg:** {_mcv:g} pts/sprint")
            lines.append(f"- **Delivered avg:** {_mdv:g} pts/sprint ({_mdp}% accuracy)")
    if per_dev and isinstance(per_dev, (int, float)):
        lines.append(f"- **Per developer:** {per_dev} pts/sprint")
    if vel > 0:
        lines.append(f"- **Variance:** ±{std} ({std / vel * 100:.0f}%)")
    if profile.sprint_completion_rate > 0:
        lines.append(f"- **Completion rate:** {_format_pct(profile.sprint_completion_rate)}")
    if profile.spillover.carried_over_pct > 0:
        lines.append(f"- **Spillover:** {_format_pct(profile.spillover.carried_over_pct)} carried over")

    # Velocity trend
    vt = ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend = vt["trend"]
        slope = vt.get("slope", 0)
        first_v = vt.get("first_velocity", 0)
        last_v = vt.get("last_velocity", 0)
        lines.append(f"- **Trend:** {trend.capitalize()} ({first_v} → {last_v}, {slope:+.1f}/sprint)")
    lines.append("")

    # ── Spillover Root Causes ───────────────────────────────────────
    spill_corr = ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_disc = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        has_spill = any(v > 0 for d in (by_size, by_disc, by_tasks) if isinstance(d, dict) for v in d.values())
        if has_spill:
            lines.extend(["## Spillover Root Causes", ""])
            if by_size:
                parts = " · ".join(f"{sz}pt={pct:.0f}%" for sz, pct in sorted(by_size.items(), key=lambda x: int(x[0])))
                lines.append(f"- **By size:** {parts}")
            if by_disc:
                parts = " · ".join(f"{d}={pct:.0f}%" for d, pct in sorted(by_disc.items()))
                lines.append(f"- **By discipline:** {parts}")
            if by_tasks:
                parts = " · ".join(f"{b}={pct:.0f}%" for b, pct in by_tasks.items())
                lines.append(f"- **By task count:** {parts}")
            lines.append("")

    # ── Sprint Breakdown ────────────────────────────────────────────
    if sp_details and isinstance(sp_details, list) and sp_details:
        lines.extend(
            [
                "## Sprint Breakdown",
                "",
                "| Sprint | Pts | Done | Rate | |",
                "|--------|-----|------|------|-|",
            ]
        )
        for sd in sp_details:
            if not isinstance(sd, dict):
                continue
            name = sd.get("name", "?")
            pts = sd.get("points", 0)
            planned = sd.get("planned", 0)
            completed = sd.get("completed", 0)
            rate = sd.get("rate", 0)
            done = sd.get("done", False)
            has_shadow = sd.get("has_shadow", False)
            icon = "✓" if done else ("○" if has_shadow else "✗")
            lines.append(f"| {name} | {pts} | {completed}/{planned} | {rate}% | {icon} |")
        lines.append("")

        # Incomplete sprint analysis
        incomplete = [
            sd
            for sd in sp_details
            if isinstance(sd, dict)
            and (not sd.get("done", False) or sd.get("has_shadow", False))
            and sd.get("incomplete")
        ]
        if incomplete:
            lines.extend(["### Incomplete sprint analysis", ""])
            for sd in incomplete[:3]:
                sname = sd.get("name", "?")
                gap = sd.get("planned", 0) - sd.get("completed", 0)
                has_sh = sd.get("has_shadow", False)
                parts = []
                if gap > 0:
                    parts.append(f"{gap} stories not completed")
                if has_sh:
                    parts.append("shadow spillover")
                lines.append(f"**{sname}** — {' + '.join(parts)}")
                for item in sd.get("incomplete", [])[:3]:
                    if not isinstance(item, dict):
                        continue
                    ek = item.get("issue_key", "")
                    sm = item.get("summary", "")
                    shadow = item.get("shadow", False)
                    pts_v = item.get("points", 0)
                    detail = " (re-created)" if shadow else (f" ({pts_v}pts)" if pts_v else "")
                    lines.append(f"  - `{ek}` {sm}{detail}")
                lines.append("")

    # ── Shadow Spillover ────────────────────────────────────────────
    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        lines.extend(
            [
                f"## Shadow Spillover ({len(shadow)} re-created stories)",
                "",
                "Closed in one sprint but re-created in the next:",
                "",
            ]
        )
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            ek = sh.get("issue_key", "")
            title = sh.get("title", "")
            from_sp = sh.get("from_sprint", "")
            to_sp = sh.get("to_sprint", "")
            lines.append(f"- `{ek}` {title}")
            if from_sp or to_sp:
                lines.append(f"  - {from_sp} → {to_sp}")
        lines.append("")

    # ── Scope Analysis (appended to sprint section) ─────────────────
    _md_scope = ex.get("scope_changes", {})
    if isinstance(_md_scope, dict) and _md_scope.get("totals"):
        _md_t = _md_scope["totals"]
        _md_a = _md_t.get("added_mid_sprint", 0)
        _md_r = _md_t.get("re_estimated", 0)
        _md_n = _md_t.get("total_stories", 0)
        _md_cv = _md_t.get("avg_committed_velocity", 0.0)
        _md_dv = _md_t.get("avg_delivered_velocity", 0.0)
        if _md_a > 0 or _md_r > 0 or _md_cv > 0:
            lines.append("---")
            lines.append("")
            if _md_cv > 0:
                _md_dp = round(_md_dv / _md_cv * 100)
                lines.append(f"Committed **{_md_cv:g}** → Delivered **{_md_dv:g}** pts/sprint avg ({_md_dp}% accuracy)")
            if _md_n > 0 and (_md_a > 0 or _md_r > 0):
                lines.append(
                    f"- {_md_a} added mid-sprint ({_md_a * 100 // _md_n}%) "
                    f"· {_md_r} re-estimated ({_md_r * 100 // _md_n}%)"
                )
            lines.append("")
            _md_tls = _md_scope.get("timelines", [])
            _md_we = [t for t in _md_tls if hasattr(t, "change_events") and t.change_events]
            for tl in _md_we[-4:]:
                _d = tl.scope_change_total
                _p = round(_d / tl.committed_pts * 100) if tl.committed_pts else 0
                _ds = f"+{_d:g}" if _d > 0 else f"{_d:g}"
                _ns = len(tl.daily_snapshots[0].stories_in_sprint) if tl.daily_snapshots else 0
                _nf = len(tl.daily_snapshots[-1].stories_in_sprint) if tl.daily_snapshots else 0
                lines.append(f"### {tl.sprint_name} — {_ds} scope ({_p:+d}%)")
                lines.append("")
                lines.append(f"Committed {tl.committed_pts:g} pts ({_ns} stories)")
                lines.append("")
                for ev in tl.change_events[:5]:
                    ct = ev.change_type.replace("re_estimated_", "re-est ").replace("_", " ")
                    evd = f"+{ev.delta_pts:g}" if ev.delta_pts > 0 else f"{ev.delta_pts:g}"
                    sm = f" — {ev.summary}" if ev.summary else ""
                    lines.append(f"- {evd} pts `{ev.issue_key}` {ct}{sm}")
                if len(tl.change_events) > 5:
                    lines.append(f"- ... +{len(tl.change_events) - 5} more")
                lines.append("")
                lines.append(f"Final {tl.final_pts:g} pts ({_nf} stories) · Delivered {tl.delivered_pts:g} pts")
                lines.append("")
            _md_chains = _md_scope.get("carry_over_chains", [])
            if _md_chains:
                lines.append(f"**{len(_md_chains)} stories bounced across 3+ sprints:**")
                for ch in _md_chains[:5]:
                    if isinstance(ch, dict):
                        ek = ch.get("issue_key", "")
                        sps = " → ".join(str(s) for s in ch.get("sprints", []))
                        lines.append(f"- `{ek}` {sps}")
                lines.append("")

    # ── Discipline-Specific Calibration ─────────────────────────────
    disc_cal = ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        lines.extend(["## Calibration by Discipline", ""])
        for disc, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            lines.append(f"### {disc}")
            lines.append("")
            lines.append("| Points | Cycle | Variance | Samples | Spillover |")
            lines.append("|--------|-------|----------|---------|-----------|")
            for e in entries:
                if not isinstance(e, dict):
                    continue
                pts = e.get("points", 0)
                avg_d = e.get("avg_cycle_days", 0)
                var = e.get("variance", 0)
                samples = e.get("samples", 0)
                sp = e.get("spill_pct", 0)
                var_str = f"±{var:.0f}d" if var > 0 else "—"
                sp_str = f"{sp:.0f}%" if sp > 0 else "—"
                lines.append(f"| {pts}pts | {avg_d:.0f}d | {var_str} | {samples} | {sp_str} |")
            lines.append("")

    # ── Point Calibration ───────────────────────────────────────────
    cals = [c for c in profile.point_calibrations if c.sample_count > 0]
    conf_levels = ex.get("confidence_levels", {})
    if cals:
        lines.extend(
            [
                "## What Each Point Value Means",
                "",
                "| Points | Cycle time | Samples | Tasks | Slip | Confidence |",
                "|--------|-----------|---------|-------|------|------------|",
            ]
        )
        for c in cals:
            pts_label = f"{c.point_value}pt" if c.point_value == 1 else f"{c.point_value}pts"
            conf = conf_levels.get(c.point_value, "") if isinstance(conf_levels, dict) else ""
            conf_str = conf.upper() if conf == "high" else (conf if conf else "")
            lines.append(
                f"| {pts_label} | {c.avg_cycle_time_days:.0f}d | {c.sample_count} "
                f"| ~{c.typical_task_count:.0f} | {_format_pct(c.overshoot_pct)} | {conf_str} |"
            )
            if c.common_patterns:
                lines.append(f"  - Typical: {', '.join(c.common_patterns)}")
            # Issue key examples
            cal_examples = ex.get(f"calibration_{c.point_value}pt", [])
            for ce in cal_examples[:2]:
                if isinstance(ce, dict):
                    ek = ce.get("issue_key", "")
                    sm = ce.get("summary", "")
                    detail = ce.get("detail", "")
                    lines.append(f"  - `{ek}` {sm}{f' — {detail}' if detail else ''}")
        lines.append("")

    # ── Story Shapes ────────────────────────────────────────────────
    shapes = [s for s in profile.story_shapes if s.sample_count > 0]
    if shapes:
        lines.extend(
            [
                "## Story Shape by Discipline",
                "",
                "| Discipline | Avg pts | Avg ACs | Avg tasks | Samples |",
                "|-----------|---------|---------|-----------|---------|",
            ]
        )
        for s in shapes:
            lines.append(
                f"| {s.discipline} | {s.avg_points} | {s.avg_ac_count} | {s.avg_task_count} | {s.sample_count} |"
            )
        lines.append("")

    # ── Task Decomposition ──────────────────────────────────────────
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_tasks", 0) > 0:
        lines.extend(["## Task Decomposition", ""])
        lines.append(f"- **Stories with tasks:** {td['stories_with_tasks']} / {td['total_stories']}")
        lines.append(f"- **Total tasks:** {td['total_tasks']}")
        lines.append(f"- **Avg tasks/story:** {td['avg_tasks_per_story']}")
        lines.append(f"- **Task completion:** {_format_pct(td['task_completion_rate'])}")
        type_dist = td.get("type_distribution", {})
        if type_dist:
            lines.append("")
            for cat, pct in type_dist.items():
                lines.append(f"  - {cat}: {_format_pct(pct)}")

        bottlenecks = td.get("bottlenecks", [])
        for cat, rate_val, count in bottlenecks:
            lines.append(f"- **{cat} bottleneck:** only {rate_val}% completion ({count} tasks)")

        common_tasks = td.get("common_tasks", [])
        if common_tasks:
            lines.extend(["", "Common task patterns:"])
            for title, cnt in common_tasks[:4]:
                lines.append(f"  - {title} ×{cnt}")

        assignees = td.get("task_assignees", {})
        if assignees:
            lines.extend(["", "Task assignees:"])
            for name, cnt in list(assignees.items())[:5]:
                lines.append(f"  - {name}: {cnt} tasks")
        lines.append("")

    # ── DoD Signals ─────────────────────────────────────────────────
    dod = profile.dod_signal
    dod_items_keyed: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_items_keyed.append(("Testing mentioned", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_items_keyed.append(("PR linked", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_items_keyed.append(("Code review", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_items_keyed.append(("Deploy", dod.stories_with_deploy_mention_pct, "dod_deploy"))
    if dod_items_keyed:
        lines.extend(["## Definition of Done (inferred)", ""])
        for label, pct, ekey in dod_items_keyed:
            ex_items = ex.get(ekey, [])
            ex_str = ""
            if ex_items and isinstance(ex_items, list) and ex_items:
                e0 = ex_items[0]
                if isinstance(e0, dict):
                    ex_str = f" — e.g. `{e0.get('issue_key', '')}` {e0.get('summary', '')[:30]}"
            lines.append(f"- **{label}:** {_format_pct(pct)}{ex_str}")
        if dod.common_checklist_items:
            lines.append(f"- **Common signals:** {', '.join(dod.common_checklist_items[:6])}")
        lines.append("")

    # ── Proposed DoD ───────────────────────────────────────────────
    pdod = ex.get("proposed_dod", {})
    if isinstance(pdod, dict) and pdod.get("items"):
        lines.extend(["## Proposed Definition of Done", ""])
        pdod_summary = pdod.get("summary", "")
        if pdod_summary:
            lines.append(f"**{pdod_summary}**")
            lines.append("")
        lines.extend(
            [
                "| Practice | Status | Evidence | Action |",
                "|----------|--------|----------|--------|",
            ]
        )
        _md_st_icon = {"established": "\u2713", "emerging": "\u25cb", "missing": "\u2717"}
        for item in pdod["items"]:
            st = item.get("status", "missing")
            sig = item.get("signals", "no evidence")
            lines.append(
                f"| {item.get('practice', '')} "
                f"| {_md_st_icon.get(st, '?')} {st} "
                f"| {sig} "
                f"| {item.get('recommendation', '')} |"
            )
        dod_ordering = pdod.get("ordering", [])
        if len(dod_ordering) >= 2:
            lines.append(f"**Typical order:** {' → '.join(dod_ordering)}")
        custom_steps = pdod.get("custom_steps", [])
        if custom_steps:
            parts = ", ".join(f'"{cs["title"]}" ({cs["pct"]}%)' for cs in custom_steps[:4])
            lines.append(f"**Team-specific steps:** {parts}")
        lines.append("")

    # ── Writing Patterns ────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_items: list[tuple[str, str]] = []
    if wp.uses_given_when_then:
        wp_items.append(("AC format", "Given/When/Then ✓"))
    if wp.median_ac_count > 0:
        wp_items.append(("Median ACs/story", str(wp.median_ac_count)))
    if wp.median_task_count_per_story > 0:
        wp_items.append(("Median tasks/story", str(wp.median_task_count_per_story)))
    if wp.subtask_label_distribution:
        parts = " · ".join(f"{lbl} {int(pct * 100)}%" for lbl, pct in wp.subtask_label_distribution[:5])
        wp_items.append(("Sub-task types", parts))
    if wp.common_personas:
        wp_items.append(("Personas", ", ".join(wp.common_personas[:5])))
    if wp_items:
        lines.extend(["## Writing Patterns", ""])
        for label, val in wp_items:
            lines.append(f"- **{label}:** {val}")
        lines.append("")

    # ── Repository Activity ─────────────────────────────────────────
    repos = ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        avg_cts = repos.get("repo_avg_cycle_time", {})
        lines.extend(
            [
                "## Repository Activity",
                "",
                "| Repository | Stories | Share | Avg cycle |",
                "|-----------|---------|-------|-----------|",
            ]
        )
        for r in repos["top_repos"][:8]:
            if isinstance(r, dict):
                rname = r.get("repo", "")
                avg_ct = avg_cts.get(rname) if isinstance(avg_cts, dict) else None
                ct_str = f"{avg_ct:.0f}d" if avg_ct else "—"
                lines.append(f"| {rname} | {r.get('stories', 0)} | {_format_pct(r.get('pct', 0))} | {ct_str} |")
        lines.append("")

        spill_repos = repos.get("spillover_repos", [])
        if spill_repos and isinstance(spill_repos, list):
            lines.append("**Spillover-prone repos:**")
            for sr in spill_repos[:3]:
                if isinstance(sr, dict):
                    lines.append(
                        f"- **{sr.get('repo', '')}** — "
                        f"{sr.get('spill_rate', 0)}% spillover ({sr.get('spills', 0)} times)"
                    )
            lines.append("")

        by_pts = repos.get("by_pts", {})
        if by_pts and isinstance(by_pts, dict):
            lines.append("**Repos by story size:**")
            for pts_key in sorted(by_pts.keys(), key=lambda x: int(x)):
                pt_repos = by_pts[pts_key]
                if pt_repos:
                    lines.append(f"- {pts_key}pt: {', '.join(str(r) for r in pt_repos[:3])}")
            lines.append("")

    # ── Epic Sizing ─────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        lines.extend(["## Epic Sizing", ""])
        lines.append(f"- **Avg stories/epic:** {epic.avg_stories_per_epic:.0f}")
        lines.append(f"- **Avg points/epic:** {epic.avg_points_per_epic:.0f}")
        lo, hi = epic.typical_story_count_range
        if lo > 0 or hi > 0:
            lines.append(f"- **Story count range:** {lo}–{hi}")
        lines.append("")

    # ── Recommendations (all 13 types, matching TUI) ────────────────
    recs: list[tuple[str, str]] = []
    if vel > 0:
        var_pct = std / vel * 100
        if var_pct > 35:
            recs.append(("High velocity variance", f"Velocity swings ±{var_pct:.0f}%."))
    if profile.sprint_completion_rate > 0 and profile.sprint_completion_rate < 60:
        recs.append(("Low sprint completion", f"Only {profile.sprint_completion_rate:.0f}% completes."))
    if profile.spillover.carried_over_pct > 15:
        recs.append(("Frequent spillover", f"{profile.spillover.carried_over_pct:.0f}% carry over."))
    for c in cals:
        if c.point_value >= 8 and c.avg_cycle_time_days > 60:
            recs.append((f"{c.point_value}-pt stories too large", f"Take {c.avg_cycle_time_days:.0f}d avg."))
            break
    dod = profile.dod_signal
    if 0 < dod.stories_with_testing_mention_pct < 15:
        recs.append(("Testing rarely mentioned", f"Only {dod.stories_with_testing_mention_pct:.0f}%."))
    if 0 < dod.stories_with_pr_link_pct < 20:
        recs.append(("Low PR linkage", f"Only {dod.stories_with_pr_link_pct:.0f}%."))
    md_rec_count = ex.get("recurring_count", 0)
    md_del_count = ex.get("delivery_count", 0)
    if isinstance(md_rec_count, int) and isinstance(md_del_count, int):
        total = md_rec_count + md_del_count
        if total > 0 and md_rec_count / total > 0.3:
            recs.append(("High recurring overhead", f"{md_rec_count}/{total} are recurring."))
    per_dev = ex.get("per_dev_velocity", 0)
    team_sz = ex.get("team_size", 0)
    if team_sz and isinstance(team_sz, int) and per_dev and isinstance(per_dev, (int, float)) and per_dev < 3:
        recs.append(("Low per-developer output", f"Avg {per_dev} pts/sprint per dev."))
    _repos = ex.get("repositories", {})
    if isinstance(_repos, dict):
        for sr in _repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append((f"{sr['repo']} high spillover", f"{sr['spill_rate']}% of stories spill."))
    _shadow = ex.get("shadow_spillover", [])
    if isinstance(_shadow, list) and len(_shadow) >= 2:
        recs.append(("Shadow spillover", f"{len(_shadow)} stories re-created across sprints."))
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict):
        if td.get("task_completion_rate", 100) < 60:
            recs.append(("Low task completion", f"Only {td['task_completion_rate']}% of tasks done."))
        for cat, rate_val, count in td.get("bottlenecks", []):
            recs.append((f"{cat} bottleneck", f"Only {rate_val}% completion ({count} tasks)."))
        sw = td.get("stories_with_tasks", 0)
        tot = td.get("total_stories", 0)
        if tot > 10 and sw > 0 and sw / tot < 0.3:
            recs.append(("Low task breakdown", f"Only {sw}/{tot} stories have sub-tasks."))

    # Scope change recommendations
    _md_sc = ex.get("scope_changes", {})
    if isinstance(_md_sc, dict) and _md_sc.get("totals"):
        _md_sct = _md_sc["totals"]
        _md_n = _md_sct.get("total_stories", 0)
        _md_cv = _md_sct.get("avg_committed_velocity", 0.0)
        _md_dv = _md_sct.get("avg_delivered_velocity", 0.0)
        if _md_cv > 0 and _md_dv / _md_cv < 0.7:
            _dp = round(_md_dv / _md_cv * 100)
            recs.append(("Low delivery accuracy", f"Team delivers only {_dp}% of committed scope."))
        if _md_n > 0:
            _md_a = _md_sct.get("added_mid_sprint", 0)
            _md_r = _md_sct.get("re_estimated", 0)
            if _md_a / _md_n > 0.15:
                recs.append(
                    (
                        "High mid-sprint scope additions",
                        f"{_md_a}/{_md_n} stories ({_md_a / _md_n * 100:.0f}%) added after sprint start.",
                    )
                )
            if _md_r / _md_n > 0.15:
                recs.append(
                    (
                        "Frequent re-estimation",
                        f"{_md_r}/{_md_n} stories ({_md_r / _md_n * 100:.0f}%) re-estimated mid-sprint.",
                    )
                )
        _md_sps = _md_sc.get("per_sprint", [])
        _md_hc = [s for s in _md_sps if s.get("scope_churn", 0) > 0.3]
        if len(_md_hc) >= 2:
            _cn = ", ".join(s.get("name", "?") for s in _md_hc[:3])
            recs.append(("High scope churn", f"{len(_md_hc)} sprints had >30% churn ({_cn})."))
        _md_ch = _md_sc.get("carry_over_chains", [])
        if len(_md_ch) >= 3:
            recs.append(("Carry-over chains", f"{len(_md_ch)} stories bounced across 3+ sprints."))

    _md_pdod = ex.get("proposed_dod", {})
    if isinstance(_md_pdod, dict) and _md_pdod.get("health") == "weak":
        _mm = [i["practice"] for i in _md_pdod.get("items", []) if i.get("status") == "missing"]
        recs.append(
            (
                "No consistent DoD",
                f"No consistent DoD found. {', '.join(_mm[:3])} show no evidence. Create a team DoD checklist.",
            )
        )
    elif isinstance(_md_pdod, dict) and _md_pdod.get("health") == "moderate":
        _me = [i["practice"] for i in _md_pdod.get("items", []) if i.get("status") == "emerging"]
        if _me:
            recs.append(
                (
                    "Create a formal DoD",
                    f"{', '.join(_me[:3])} are inconsistent. Write a shared DoD checklist.",
                )
            )

    if recs:
        lines.extend(["## Recommendations", ""])
        for title, desc in recs:
            lines.append(f"- **{title}:** {desc}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Exported team profile Markdown to %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Analysis log — structured record of each analysis run
# ---------------------------------------------------------------------------


def write_analysis_log(
    profile: TeamProfile,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    duration_secs: float = 0.0,
) -> Path:
    """Write a structured analysis log to ~/.scrum-agent/logs/.

    Each analysis run gets its own log file with full profile data, examples,
    and timing info. This provides an auditable history of every analysis run,
    sorted into the project's export directory for easy discovery.

    Returns the path to the generated log file.
    """
    import json

    log_dir = Path.home() / ".scrum-agent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"team-analysis-{profile.project_key.lower()}-{ts}.log"

    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections: list[str] = [
        f"Team Analysis Log — {profile.source}/{profile.project_key}",
        f"Generated: {gen_ts}",
        f"Duration: {duration_secs:.1f}s" if duration_secs > 0 else "",
        "",
        "=" * 60,
        "",
        f"Sprints analysed: {profile.sample_sprints}",
        f"Stories analysed: {profile.sample_stories}",
        f"Velocity avg:     {profile.velocity_avg} pts/sprint",
        f"Velocity stddev:  ±{profile.velocity_stddev}",
        f"Completion rate:  {_format_pct(profile.sprint_completion_rate)}",
        f"Estimation accuracy: {_format_pct(profile.estimation_accuracy_pct)}",
    ]

    if sprint_names:
        sections.extend(["", "Sprints:"])
        for name in sprint_names:
            sections.append(f"  - {name}")

    if profile.spillover.carried_over_pct > 0:
        sections.extend(
            [
                "",
                "Spillover:",
                f"  Carried over: {_format_pct(profile.spillover.carried_over_pct)}",
                f"  Avg spillover pts: {profile.spillover.avg_spillover_pts}",
            ]
        )
        if profile.spillover.most_common_spillover_reason:
            sections.append(f"  Common reason: {profile.spillover.most_common_spillover_reason}")

    if profile.point_calibrations:
        sections.extend(["", "Point Calibrations:"])
        for c in profile.point_calibrations:
            if c.sample_count == 0:
                continue
            sections.append(
                f"  {c.point_value}pt: {c.avg_cycle_time_days}d avg, "
                f"{c.sample_count} samples, {_format_pct(c.overshoot_pct)} slip, "
                f"~{c.typical_task_count} tasks"
            )
            if c.common_patterns:
                sections.append(f"       patterns: {', '.join(c.common_patterns)}")

    if profile.story_shapes:
        sections.extend(["", "Story Shapes:"])
        for s in profile.story_shapes:
            sections.append(
                f"  {s.discipline}: avg {s.avg_points}pts, "
                f"{s.avg_ac_count} ACs, {s.avg_task_count} tasks "
                f"({s.sample_count} samples)"
            )

    dod = profile.dod_signal
    if dod.stories_with_pr_link_pct > 0 or dod.stories_with_review_mention_pct > 0:
        sections.extend(["", "DoD Signals:"])
        if dod.stories_with_pr_link_pct > 0:
            sections.append(f"  PR linked:     {_format_pct(dod.stories_with_pr_link_pct)}")
        if dod.stories_with_review_mention_pct > 0:
            sections.append(f"  Code review:   {_format_pct(dod.stories_with_review_mention_pct)}")
        if dod.stories_with_testing_mention_pct > 0:
            sections.append(f"  Testing:       {_format_pct(dod.stories_with_testing_mention_pct)}")
        if dod.stories_with_deploy_mention_pct > 0:
            sections.append(f"  Deploy:        {_format_pct(dod.stories_with_deploy_mention_pct)}")
        if dod.common_checklist_items:
            sections.append(f"  Checklist:     {', '.join(dod.common_checklist_items)}")

    wp = profile.writing_patterns
    if wp.median_ac_count > 0 or wp.uses_given_when_then:
        sections.extend(["", "Writing Patterns:"])
        if wp.uses_given_when_then:
            sections.append("  AC format: Given/When/Then")
        if wp.median_ac_count > 0:
            sections.append(f"  Median ACs/story: {wp.median_ac_count}")
        if wp.median_task_count_per_story > 0:
            sections.append(f"  Median tasks/story: {wp.median_task_count_per_story}")
        if wp.common_personas:
            sections.append(f"  Personas: {', '.join(wp.common_personas)}")

    # Full profile JSON for machine-readable recovery
    sections.extend(["", "=" * 60, "", "Raw profile JSON:", ""])
    try:
        sections.append(json.dumps(asdict(profile), indent=2, ensure_ascii=False, default=str))
    except Exception:
        sections.append("(serialisation failed)")

    # Examples JSON if provided
    if examples:
        sections.extend(["", "=" * 60, "", "Examples JSON:", ""])
        try:
            sections.append(json.dumps(examples, indent=2, ensure_ascii=False, default=str))
        except Exception:
            sections.append("(serialisation failed)")

    log_path.write_text("\n".join(sections), encoding="utf-8")
    logger.info("Analysis log written to %s", log_path)
    return log_path
