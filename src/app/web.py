from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.auth import SessionContext, get_session_context
from app.db import engine
from app.models import DatasetSnapshot, JobDefinition
from app.services.dashboard import build_overview_summary
from app.services.findings import filter_identity_findings
from app.services.runs import get_run_detail, latest_runs_summary, list_runs


router = APIRouter()


_HEAD_ASSETS = """
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;700;900&display=block" rel="stylesheet" />
<link rel="stylesheet" href="/static/styles/app.css" />
<style>
  [hidden] {
    display: none !important;
  }
}</style>
"""


_CLIENT_SCRIPT = """
<script>
  const banner = document.getElementById("status-banner");

  function bannerToneClasses(tone) {
    if (tone === "error") {
      return "neo-status-banner neo-status-banner--accent";
    }
    if (tone === "success") {
      return "neo-status-banner neo-status-banner--secondary";
    }
    return "neo-status-banner neo-status-banner--paper";
  }

  function showBanner(message, tone = "info") {
    if (!banner) return;
    banner.className = bannerToneClasses(tone);
    banner.textContent = message;
    banner.hidden = false;
    banner.setAttribute("role", tone === "error" ? "alert" : "status");
  }

  async function sendJson(url, options) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });

    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }

    if (!response.ok) {
      const message =
        payload?.detail?.error?.message ||
        payload?.error?.message ||
        payload?.detail ||
        "Request failed";
      throw new Error(message);
    }

    return payload;
  }

  function parseCsvList(value) {
    return value.split(",").map((item) => item.trim()).filter(Boolean);
  }

  const loginForm = document.querySelector("[data-login-form]");
  if (loginForm) {
    loginForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(loginForm);
      try {
        await sendJson("/api/v1/auth/login", {
          method: "POST",
          body: JSON.stringify({
            username: formData.get("username"),
            password: formData.get("password"),
          }),
        });
        window.location.href = loginForm.dataset.next || "/ui/overview";
      } catch (error) {
        showBanner(error.message, "error");
      }
    });
  }

  const logoutButton = document.querySelector("[data-logout-button]");
  if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
      try {
        await sendJson("/api/v1/auth/logout", {
          method: "POST",
          body: JSON.stringify({}),
        });
        window.location.href = "/ui/overview";
      } catch (error) {
        showBanner(error.message, "error");
      }
    });
  }

  const configForm = document.querySelector("[data-config-form]");
  if (configForm) {
    configForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(configForm);
      let advancedParams = {};
      const rawAdvanced = String(formData.get("advanced_params_json") || "").trim();
      if (rawAdvanced) {
        try {
          advancedParams = JSON.parse(rawAdvanced);
        } catch (_error) {
          showBanner("Advanced params JSON tidak valid.", "error");
          return;
        }
      }

      delete advancedParams.base_url;
      delete advancedParams.sql_lab_url;
      delete advancedParams.source_data_path;
      delete advancedParams.batching_strategy;

      const batchingValues = parseCsvList(String(formData.get("batching_values") || ""));
      const batchingParam = String(formData.get("batching_param") || "").trim();
      const params = {
        ...advancedParams,
        base_url: String(formData.get("base_url") || "").trim(),
        sql_lab_url: String(formData.get("sql_lab_url") || "").trim(),
        source_data_path: String(formData.get("source_data_path") || "").trim(),
      };

      if (batchingParam || batchingValues.length) {
        params.batching_strategy = {
          type: "explicit_list",
          param: batchingParam || "level_2_code",
          values: batchingValues,
        };
      }

      try {
        await sendJson("/api/v1/config/job-definition", {
          method: "PATCH",
          body: JSON.stringify({
            name: formData.get("name"),
            execution_mode: formData.get("execution_mode"),
            sql_template: formData.get("sql_template"),
            params_schema_json: params,
            merge_key_columns_json: parseCsvList(String(formData.get("merge_keys") || "")),
            identity_columns_json: parseCsvList(String(formData.get("identity_columns") || "")),
          }),
        });
        showBanner("Configuration updated.", "success");
      } catch (error) {
        showBanner(error.message, "error");
      }
    });
  }

  document.querySelectorAll("[data-run-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const endpoint = button.dataset.endpoint;
      const payload = JSON.parse(button.dataset.payload || "{}");
      try {
        await sendJson(endpoint, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        window.location.reload();
      } catch (error) {
        showBanner(error.message, "error");
      }
    });
  });
</script>
"""


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _json_text(value: object) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2))


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "Belum ada data"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%d %b %Y %H:%M UTC")


def _page_url(path: str, **params: object) -> str:
    cleaned = {key: value for key, value in params.items() if value not in (None, "", [])}
    if not cleaned:
        return path
    return f"{path}?{urlencode(cleaned, doseq=True)}"


def _latest_job_definition(session: Session) -> JobDefinition | None:
    jobs = session.exec(select(JobDefinition)).all()
    if not jobs:
        return None
    return max(jobs, key=lambda item: item.id or 0)


def _latest_snapshot(session: Session) -> DatasetSnapshot | None:
    snapshots = session.exec(select(DatasetSnapshot)).all()
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: (item.created_at or datetime.min, item.id or 0))


def _badge(label: str, value: object, tone: str = "neutral") -> str:
    palette = {
        "accent": "neo-chip neo-chip--muted",
        "warning": "neo-chip neo-chip--secondary",
        "critical": "neo-chip neo-chip--accent",
        "muted": "neo-chip neo-chip--muted",
        "neutral": "neo-chip",
    }
    tone_class = palette.get(tone, palette["neutral"])
    return (
        f"<span class='{tone_class}'>"
        f"<span>{_escape(label)}</span>"
        f"<span class='neo-chip__value'>{_escape(value)}</span>"
        "</span>"
    )


def _status_badge(status: str) -> str:
    normalized = status.lower()
    tone = {
        "completed": "neo-pill neo-pill--secondary",
        "failed": "neo-pill neo-pill--accent",
        "pending": "neo-pill neo-pill--muted",
        "idle": "neo-pill neo-pill--paper",
    }.get(normalized, "neo-pill neo-pill--paper")
    return f"<span class='{tone}'>{_escape(status)}</span>"


def _nav_link(active_page: str, key: str, label: str, href: str) -> str:
    is_active = active_page == key
    classes = "neo-nav-link"
    if is_active:
        classes += " neo-nav-link--active"
    current = " aria-current='page'" if is_active else ""
    return f"<a class='{classes}' href='{href}'{current}>{label}</a>"


def _metric_card(title: str, value: str, supporting: str | None = None) -> str:
    support_html = (
        f"<p class='neo-panel-copy mt-4 max-w-sm text-sm'>{_escape(supporting)}</p>"
        if supporting
        else ""
    )
    return f"""
      <article class='neo-panel neo-panel--paper neo-lift p-6 sm:p-7'>
        <p class='neo-panel-kicker'>{_escape(title)}</p>
        <p class='neo-panel-value mt-5 text-4xl sm:text-5xl'>{_escape(value)}</p>
        {support_html}
      </article>
    """


def _section_card(title: str, copy: str, body: str, extra_classes: str = "") -> str:
    return f"""
      <section class='neo-panel neo-panel--paper neo-lift p-6 sm:p-8 {extra_classes}'>
        <h2 class='neo-panel-title text-3xl sm:text-4xl'>{_escape(title)}</h2>
        <p class='neo-panel-copy mt-4 text-sm'>{_escape(copy)}</p>
        <div class='mt-6'>{body}</div>
      </section>
    """


def _overview_metric_card(
    title: str,
    value: str,
    supporting: str,
    sticker: str,
    icon: str,
    rotation_class: str = "",
) -> str:
    return f"""
      <article class='neo-panel neo-panel--paper neo-lift {rotation_class}'>
        <div class='border-b-4 border-black px-6 pb-6 pt-6 sm:px-7'>
          <div class='flex items-start justify-between gap-4'>
            <div class='flex h-[4.5rem] w-[4.5rem] items-center justify-center border-4 border-black bg-[#FF6B6B] text-3xl font-black leading-none shadow-[4px_4px_0_0_#000]'>
              {_escape(icon)}
            </div>
            <div class='flex h-[4.25rem] w-[4.25rem] shrink-0 items-center justify-center border-l-4 border-b-4 border-black bg-[#FFD93D] text-2xl font-black leading-none'>
              {_escape(sticker)}
            </div>
          </div>
          <p class='mt-6 text-[0.82rem] font-black uppercase tracking-[0.18em] text-black'>{_escape(title)}</p>
          <p class='mt-4 text-4xl font-black leading-[0.86] tracking-[-0.06em] text-black sm:text-5xl'>{_escape(value)}</p>
        </div>
        <div class='px-6 py-5 sm:px-7'>
          <p class='neo-panel-copy text-base'>{_escape(supporting)}</p>
        </div>
      </article>
    """


def _overview_mix_card(
    title: str,
    copy: str,
    body: str,
    sticker: str,
    panel_class: str,
    watermark: str,
) -> str:
    return f"""
      <section class='neo-panel neo-lift {panel_class} p-6 sm:p-8'>
        <div class='absolute left-5 top-0 h-4 w-24 -translate-y-1/2 border-4 border-black bg-white'></div>
        <span class='neo-pill neo-pill--paper absolute -top-5 right-5'>{_escape(sticker)}</span>
        <p class='neo-outline-display pointer-events-none absolute -right-3 bottom-0 hidden text-[5.5rem] font-black leading-none opacity-20 xl:block'>{_escape(watermark)}</p>
        <div class='relative'>
          <h2 class='neo-panel-title mt-5 text-3xl sm:text-4xl'>{_escape(title)}</h2>
          <p class='neo-panel-copy mt-4 max-w-2xl text-sm'>{_escape(copy)}</p>
          <div class='mt-8 flex flex-wrap gap-4'>{body}</div>
        </div>
      </section>
    """


def _render_shell(
    request: Request,
    title: str,
    eyebrow: str,
    active_page: str,
    body: str,
    accent_copy: str,
) -> str:
    session = get_session_context(request)
    nav_items = [
        ("overview", "Overview", "/ui/overview"),
        ("findings", "Findings", "/ui/findings"),
        ("login", "Login", "/ui/login"),
    ]
    if session.role == "admin":
        nav_items.extend(
            [
                ("configuration", "Configuration", "/ui/configuration"),
                ("run-control", "Run Control", "/ui/run-control"),
            ]
        )

    nav_links = "".join(_nav_link(active_page, key, label, href) for key, label, href in nav_items)
    role_badge_class = "neo-pill neo-pill--muted" if session.role == "admin" else "neo-pill neo-pill--paper"
    role_badge = f"<span class='{role_badge_class}'>{_escape(session.role)} mode</span>"
    auth_action = (
        "<button type='button' data-logout-button "
        "class='neo-action neo-action--paper'>"
        "Sign out</button>"
        if session.role == "admin"
        else ""
    )
    header_actions = f"<div class='flex flex-wrap items-center gap-3'>{auth_action}</div>" if auth_action else ""

    mode_copy = (
        "Observe data freshness and anomaly findings tanpa membuka execution control."
        if session.role == "viewer"
        else "Admin mode membuka konfigurasi, trigger eksekusi, dan monitoring sambil menjaga viewer surface tetap sederhana."
    )

    return f"""<!doctype html>
<html lang="id">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape(title)} | Superset SQL Lab Platform</title>
    {_HEAD_ASSETS}
  </head>
  <body class="bg-neo-bg text-black selection:bg-[#FFD93D] selection:text-black">
    <a href="#main-content" class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:border-4 focus:border-black focus:bg-[#FFD93D] focus:px-4 focus:py-3 focus:text-sm focus:font-black focus:uppercase focus:tracking-[0.16em] focus:text-black">Skip ke konten utama</a>

    <div class="border-b-4 border-black bg-[#FF6B6B]">
      <div class="neo-marquee px-4 py-3">
        <div class="neo-marquee-track text-sm font-black uppercase tracking-[0.16em] text-black sm:text-base">
          <span>Overview signals always on</span>
          <span>&#9733;</span>
          <span>Freshness. anomaly. review state.</span>
          <span>&#9733;</span>
          <span>Viewer safe. admin controlled.</span>
          <span>&#9733;</span>
          <span>Overview signals always on</span>
          <span>&#9733;</span>
          <span>Freshness. anomaly. review state.</span>
          <span>&#9733;</span>
          <span>Viewer safe. admin controlled.</span>
        </div>
      </div>
    </div>

    <header class="border-b-4 border-black bg-[#FFFDF5]" role="banner">
      <div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
          <div class="flex items-center gap-4">
            <span class="flex h-14 w-14 items-center justify-center border-4 border-black bg-[#FFD93D] text-3xl font-black leading-none">S</span>
            <div>
              <p class="text-[0.78rem] font-black uppercase tracking-[0.16em] text-black">Viewer + Admin deck</p>
              <p class="text-[clamp(1.9rem,4vw,3rem)] font-black uppercase leading-none tracking-[-0.05em] text-black">Superset SQL Lab</p>
            </div>
          </div>
          <div class="flex flex-col gap-4 lg:items-end">
            <nav class="hidden flex-wrap items-center gap-5 lg:flex" aria-label="Primary navigation">
              {nav_links}
            </nav>
            {header_actions}
          </div>
        </div>
        <nav class="mt-5 flex flex-wrap items-center gap-3 lg:hidden" aria-label="Primary navigation">
          {nav_links}
        </nav>
      </div>
    </header>

    <div class="pointer-events-none fixed inset-x-0 bottom-0 top-[10.5rem] neo-dots-bg opacity-[0.16]"></div>
    <div class="pointer-events-none fixed left-2 top-56 hidden h-16 w-16 -rotate-12 border-4 border-black bg-[#FF6B6B] xl:block"></div>
    <div class="pointer-events-none fixed right-12 top-72 hidden h-24 w-24 rounded-full border-4 border-black bg-[#FFD93D] xl:block"></div>

    <div class="relative mx-auto w-full max-w-7xl px-4 pb-20 pt-10 sm:px-6 lg:px-8">
      <main id="main-content" class="relative">
        <section class="grid gap-10 xl:grid-cols-[minmax(0,1.26fr)_minmax(18rem,0.74fr)]">
          <div class="relative">
            <article class="neo-panel neo-panel--paper inline-block max-w-fit -rotate-1 px-5 py-4 sm:px-7">
              <span class="text-xs font-black uppercase tracking-[0.22em] text-black">{_escape(eyebrow)}</span>
            </article>
            <article class="neo-panel neo-panel--secondary neo-lift relative mt-4 w-full max-w-5xl -rotate-2 px-6 py-5 sm:px-8 sm:py-7">
              <div class="absolute -right-4 -top-4 hidden h-12 w-12 items-center justify-center border-4 border-black bg-[#FF6B6B] text-xl font-black xl:flex">&#9733;</div>
              <h1 class="text-[clamp(3.4rem,9vw,7.2rem)] font-black uppercase leading-[0.82] tracking-[-0.07em] text-black">{_escape(title)}</h1>
            </article>
            <article class="neo-panel neo-panel--paper neo-lift relative mt-8 max-w-3xl px-5 py-5 sm:px-6 sm:py-6">
              <div class="absolute -right-5 -top-5 hidden h-12 w-12 items-center justify-center border-4 border-black bg-[#C4B5FD] text-xl font-black xl:flex">!</div>
              <div class="border-l-4 border-black pl-4">
                <p class="neo-panel-copy text-lg sm:text-xl">{_escape(accent_copy)}</p>
              </div>
            </article>
          </div>
          <aside class="neo-panel neo-panel--muted neo-lift relative rotate-2 px-6 py-6 sm:px-7" aria-label="Session context">
            <div class="relative">
              <div>{role_badge}</div>
              <p class="mt-6 text-[1.45rem] font-black uppercase leading-tight text-black">{_escape(mode_copy)}</p>
              <p class="mt-5 text-base font-bold leading-8 text-black">Tampilan ini sengaja dibuat lebih seperti poster operasional: cepat dipindai, jelas hierarkinya, dan tetap aman dipakai dengan keyboard maupun screen reader.</p>
            </div>
          </aside>
        </section>

        <div id="status-banner" class="neo-status-banner neo-status-banner--paper" role="status" aria-live="polite" hidden></div>

        {body}
      </main>
    </div>
    {_CLIENT_SCRIPT}
  </body>
</html>"""


def _render_forbidden(request: Request, title: str) -> HTMLResponse:
    body = """
      <section class='mt-8'>
        <section class='neo-panel neo-panel--accent neo-lift p-8'>
          <h2 class='neo-panel-title text-3xl sm:text-4xl'>Admin access required</h2>
          <p class='mt-4 max-w-2xl text-base font-bold leading-8 text-black/80'>Halaman ini hanya tersedia setelah login sebagai admin. Viewer tetap bisa menggunakan Overview dan Findings tanpa hambatan.</p>
          <div class='mt-6 flex flex-wrap gap-3'>
            <a href='/ui/login' class='neo-action neo-action--inverse'>Open login</a>
            <a href='/ui/overview' class='neo-action neo-action--paper'>Back to overview</a>
          </div>
        </section>
      </section>
    """
    return HTMLResponse(
        _render_shell(
            request,
            title=title,
            eyebrow="Protected Surface",
            active_page="login",
            body=body,
            accent_copy="Observation tetap terbuka untuk semua orang, tetapi control surface dikunci untuk admin.",
        ),
        status_code=403,
    )


def _require_admin_page(request: Request, title: str) -> SessionContext | HTMLResponse:
    session = get_session_context(request)
    if session.role != "admin":
        return _render_forbidden(request, title)
    return session


def _build_overview_body() -> str:
    with Session(engine) as session:
        summary = build_overview_summary(session).to_dict()

    findings_summary = summary["findings_summary"]
    latest_dataset = summary["latest_dataset"]
    anomaly_query = summary["anomaly_query"]

    severity_tones = {
        "critical": "critical",
        "warn": "warning",
        "info": "accent",
    }
    review_tones = {
        "open": "warning",
        "reviewed": "accent",
        "closed": "muted",
    }

    severity_chips = "".join(
        _badge(label, count, severity_tones.get(label, "neutral"))
        for label, count in sorted(findings_summary["by_severity"].items())
    ) or _badge("Belum ada severity", "0", "muted")
    review_chips = "".join(
        _badge(label, count, review_tones.get(label, "neutral"))
        for label, count in sorted(findings_summary["by_review_state"].items())
    ) or _badge("Belum ada review state", "0", "muted")

    summary_cards = "".join(
        [
            _overview_metric_card(
                "Latest dataset size",
                f"{latest_dataset['row_count']:,}",
                "Baris hasil dataset snapshot terbaru yang tersedia untuk viewer.",
                "01",
                "DB",
                "-rotate-1",
            ),
            _overview_metric_card(
                "Last successful refresh",
                _format_timestamp(latest_dataset["last_successful_update_at"]),
                "Menggunakan timestamp persistensi run/snapshot sebagai sumber utama.",
                "02",
                "RF",
                "rotate-1",
            ),
            _overview_metric_card(
                "Last anomaly sweep",
                _format_timestamp(anomaly_query["last_run_at"]),
                "Waktu terakhir pipeline anomaly atau findings selesai dieksekusi.",
                "03",
                "AN",
                "-rotate-1",
            ),
            _overview_metric_card(
                "Current findings mix",
                str(findings_summary["total"]),
                "Jumlah identity finding yang saat ini muncul pada agregasi.",
                "04",
                "FX",
                "rotate-1",
            ),
        ]
    )

    left_card = _overview_mix_card(
        "Findings by severity",
        "Overview tetap hanya memuat empat sinyal inti sesuai spec, jadi tidak ada trigger run, histori eksekusi, atau editor konfigurasi di halaman ini.",
        severity_chips,
        "severity",
        "neo-panel--paper",
        "severity",
    )
    right_card = _overview_mix_card(
        "Review state mix",
        "Distribusi review state disajikan dalam bentuk ringkas supaya viewer bisa cepat membaca kondisi tanpa masuk ke workflow operasi admin.",
        review_chips,
        "review",
        "neo-panel--muted",
        "review",
    )

    return f"""
      <section class='mt-16 grid gap-10'>
        <section aria-label='Overview metrics'>
          <div class='inline-flex border-4 border-black bg-black px-5 py-3 text-sm font-black uppercase tracking-[0.18em] text-white shadow-[8px_8px_0_0_#fff]'>
            No fluff. Just raw function.
          </div>
          <div class='mt-6 grid gap-6 md:grid-cols-2' aria-label='Overview metrics'>
            {summary_cards}
          </div>
        </section>
        <section class='grid gap-6 xl:grid-cols-[minmax(0,1.04fr)_minmax(20rem,0.96fr)]'>
          {left_card}
          {right_card}
        </section>
      </section>
    """


def _build_findings_body(
    identity_key: str | None,
    nks: str | None,
    kode_kab: str | None,
    severity: str | None,
    review_state: str | None,
    selected: str | None,
) -> str:
    with Session(engine) as session:
        findings = filter_identity_findings(
            session,
            identity_key=identity_key,
            nks=nks,
            kode_kab=kode_kab,
            severity=severity,
            review_state=review_state,
        )
        selected_key = selected or (findings[0].identity_key if findings else None)
        selected_finding = next((item for item in findings if item.identity_key == selected_key), None)

    result_count = len(findings)
    export_url = _page_url(
        "/api/v1/findings/export.xlsx",
        identity_key=identity_key,
        nks=nks,
        kode_kab=kode_kab,
        severity=severity,
        review_state=review_state,
    )
    active_filters = "".join(
        [
            _badge("identity", identity_key, "muted") if identity_key else "",
            _badge("nks", nks, "warning") if nks else "",
            _badge("kode kab", kode_kab, "accent") if kode_kab else "",
            _badge("severity", severity, "critical" if severity == "critical" else "warning") if severity else "",
            _badge("review", review_state, "muted") if review_state else "",
        ]
    ) or _badge("scope", "all findings", "muted")

    rows = "".join(
        f"""
          <tr class="border-b-4 border-black last:border-b-0">
            <td class="px-4 py-4 align-top font-bold text-black">
              <a class="inline-flex border-4 border-black bg-white px-3 py-2 text-sm font-black uppercase tracking-[0.08em] text-black no-underline shadow-[4px_4px_0_0_#000] transition hover:-translate-x-[2px] hover:-translate-y-[2px] hover:bg-[#FFD93D] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-black focus-visible:ring-offset-2 focus-visible:ring-offset-white" href="{_page_url('/ui/findings', identity_key=identity_key, nks=nks, kode_kab=kode_kab, severity=severity, review_state=review_state, selected=item.identity_key)}">{_escape(item.identity_key)}</a>
            </td>
            <td class="px-4 py-4 align-top text-sm font-bold text-black">{_escape(item.nks or "-")}</td>
            <td class="px-4 py-4 align-top text-sm font-bold text-black">{_escape(item.kode_kab or "-")}</td>
            <td class="px-4 py-4 align-top">{_status_badge(item.highest_severity)}</td>
            <td class="px-4 py-4 align-top">{_status_badge(item.review_state)}</td>
            <td class="px-4 py-4 align-top"><code class="border-4 border-black bg-white px-2 py-1 text-xs font-bold text-black">{_escape(", ".join(str(rule_id) for rule_id in item.rule_ids))}</code></td>
          </tr>
        """
        for item in findings
    )
    table = (
        f"""
          <div class='overflow-x-auto border-4 border-black bg-white shadow-[8px_8px_0_0_#000]'>
            <table class='min-w-full bg-white'>
              <caption class='sr-only'>Identity findings result table</caption>
              <thead class='border-b-4 border-black bg-[#FFD93D]'>
                <tr>
                  <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-black uppercase tracking-[0.2em] text-black'>Identity</th>
                  <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-black uppercase tracking-[0.2em] text-black'>NKS</th>
                  <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-black uppercase tracking-[0.2em] text-black'>Kode Kab</th>
                  <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-black uppercase tracking-[0.2em] text-black'>Severity</th>
                  <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-black uppercase tracking-[0.2em] text-black'>Review</th>
                  <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-black uppercase tracking-[0.2em] text-black'>Rules</th>
                </tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        """
        if findings
        else "<div class='border-4 border-black bg-white px-5 py-10 text-sm font-bold leading-7 text-black shadow-[8px_8px_0_0_#000]'>Belum ada findings yang cocok dengan filter saat ini.</div>"
    )

    detail = (
        f"""
          <dl class='grid gap-5 lg:grid-cols-2'>
            <div>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>Identity key</dt>
              <dd class='mt-2'><code class='border-4 border-black bg-white px-3 py-2 text-sm font-bold text-black'>{_escape(selected_finding.identity_key)}</code></dd>
            </div>
            <div>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>Highest severity</dt>
              <dd class='mt-2'>{_status_badge(selected_finding.highest_severity)}</dd>
            </div>
            <div>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>Review state</dt>
              <dd class='mt-2'>{_status_badge(selected_finding.review_state)}</dd>
            </div>
            <div>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>Rule references</dt>
              <dd class='mt-2'><code class='border-4 border-black bg-white px-3 py-2 text-sm font-bold text-black'>{_escape(", ".join(str(rule_id) for rule_id in selected_finding.rule_ids))}</code></dd>
            </div>
            <div>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>NKS</dt>
              <dd class='mt-2'><code class='border-4 border-black bg-white px-3 py-2 text-sm font-bold text-black'>{_escape(selected_finding.nks or "-")}</code></dd>
            </div>
            <div>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>Kode Kab</dt>
              <dd class='mt-2'><code class='border-4 border-black bg-white px-3 py-2 text-sm font-bold text-black'>{_escape(selected_finding.kode_kab or "-")}</code></dd>
            </div>
            <div class='lg:col-span-2'>
              <dt class='text-xs font-black uppercase tracking-[0.22em] text-black'>Identity payload summary</dt>
              <dd class='mt-2 overflow-x-auto border-4 border-black bg-black p-4 text-sm leading-7 text-white shadow-[8px_8px_0_0_#FFD93D]'><pre>{_json_text(selected_finding.identity_payload)}</pre></dd>
            </div>
          </dl>
        """
        if selected_finding
        else "<div class='border-4 border-dashed border-black bg-white px-5 py-10 text-sm font-bold leading-7 text-black'>Pilih satu finding dari hasil filter untuk melihat detail identity payload dan rule reference.</div>"
    )

    filter_form = """
      <form class='grid gap-4 md:grid-cols-2 xl:grid-cols-5' method='get' action='/ui/findings'>
        <label class='grid gap-2 text-sm font-black uppercase tracking-[0.12em] text-black'>
          <span>Identity key</span>
          <input class='neo-field' type='text' name='identity_key' value='{identity_key}' placeholder='id-001' />
        </label>
        <label class='grid gap-2 text-sm font-black uppercase tracking-[0.12em] text-black'>
          <span>NKS</span>
          <input class='neo-field' type='text' name='nks' value='{nks}' placeholder='20250434' />
        </label>
        <label class='grid gap-2 text-sm font-black uppercase tracking-[0.12em] text-black'>
          <span>Kode Kab</span>
          <input class='neo-field' type='text' name='kode_kab' value='{kode_kab}' placeholder='01' />
        </label>
        <label class='grid gap-2 text-sm font-black uppercase tracking-[0.12em] text-black'>
          <span>Severity</span>
          <select class='neo-field' name='severity'>
            <option value=''>All severities</option>
            <option value='critical' {critical_selected}>critical</option>
            <option value='warn' {warn_selected}>warn</option>
            <option value='info' {info_selected}>info</option>
          </select>
        </label>
        <label class='grid gap-2 text-sm font-black uppercase tracking-[0.12em] text-black'>
          <span>Review state</span>
          <select class='neo-field' name='review_state'>
            <option value=''>All states</option>
            <option value='open' {open_selected}>open</option>
            <option value='reviewed' {reviewed_selected}>reviewed</option>
            <option value='closed' {closed_selected}>closed</option>
          </select>
        </label>
        <div class='flex flex-wrap items-end gap-3 md:col-span-2 xl:col-span-5'>
          <button class='neo-action neo-action--secondary' type='submit'>Apply filters</button>
          <a class='neo-action neo-action--paper' href='/ui/findings'>Reset</a>
        </div>
      </form>
    """.format(
        identity_key=_escape(identity_key),
        nks=_escape(nks),
        kode_kab=_escape(kode_kab),
        critical_selected="selected" if severity == "critical" else "",
        warn_selected="selected" if severity == "warn" else "",
        info_selected="selected" if severity == "info" else "",
        open_selected="selected" if review_state == "open" else "",
        reviewed_selected="selected" if review_state == "reviewed" else "",
        closed_selected="selected" if review_state == "closed" else "",
    )

    toolbar = f"""
      <div class='flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between'>
        <div>
          <p class='text-sm font-black uppercase tracking-[0.18em] text-black'>Filtered results</p>
          <p class='mt-2 text-3xl font-black uppercase leading-none tracking-[-0.04em] text-black'>{_escape(result_count)} findings</p>
        </div>
        <div class='flex flex-wrap items-center gap-3'>
          {active_filters}
          <a class='neo-action neo-action--accent' href='{export_url}'>Export Excel</a>
        </div>
      </div>
    """

    return f"""
      <section class='mt-10 grid gap-8'>
        {_section_card('Search and filter', 'Filter sekarang bisa dipakai untuk zoom ke NKS atau kode kabupaten/kota agar analisis lebih cepat dan hasilnya bisa langsung diexport ke Excel.', filter_form)}
        {_section_card('Results', 'Hasil filter sekarang memakai area penuh agar daftar findings lebih nyaman dipindai untuk analisis kabupaten/kota, tanpa membagi fokus ke panel samping.', toolbar + "<div class='mt-6'>" + table + "</div>")}
        {_section_card('Selected finding detail', 'Detail tetap tersedia di bawah hasil filter supaya hasil utama tetap full-width, tetapi payload dan rule reference masih mudah diinspeksi saat dibutuhkan.', detail)}
      </section>
    """


def _build_login_body(request: Request) -> str:
    session = get_session_context(request)
    next_url = request.query_params.get("next", "/ui/overview")
    status_copy = (
        "Session admin aktif. Navigation item untuk Configuration dan Run Control sudah terbuka."
        if session.role == "admin"
        else "Semua pengguna mulai sebagai viewer. Login hanya dipakai untuk membuka surface kontrol."
    )

    form = f"""
      <form class='grid gap-5' data-login-form data-next='{_escape(next_url)}'>
        <label class='grid gap-2 text-sm font-semibold text-slate-700' for='login-username'>
          <span>Username</span>
            <input id='login-username' class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition placeholder:text-slate-400 focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='username' autocomplete='username' />
        </label>
        <label class='grid gap-2 text-sm font-semibold text-slate-700' for='login-password'>
          <span>Password</span>
            <input id='login-password' class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition placeholder:text-slate-400 focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='password' name='password' autocomplete='current-password' />
        </label>
        <div class='flex flex-wrap gap-3'>
          <button class='inline-flex items-center rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='submit'>Elevate to admin</button>
          <a class='inline-flex items-center rounded-full border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-900 transition hover:border-slate-400 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' href='/ui/overview'>Stay in viewer mode</a>
        </div>
      </form>
    """
    access_model = """
      <dl class='grid gap-5'>
        <div>
          <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Viewer</dt>
          <dd class='mt-2 text-sm leading-7 text-slate-700'>Overview dan Findings terbuka tanpa login.</dd>
        </div>
        <div>
          <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Admin</dt>
          <dd class='mt-2 text-sm leading-7 text-slate-700'>Configuration, Run Control, konfigurasi job, dan trigger eksekusi dibuka setelah login.</dd>
        </div>
        <div>
          <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Scope</dt>
          <dd class='mt-2 text-sm leading-7 text-slate-700'>Auth tetap sederhana dan internal. Belum ada multi-user management atau RBAC kompleks.</dd>
        </div>
      </dl>
    """
    return f"""
      <section class='mt-8 grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,0.9fr)]'>
        {_section_card('Admin login', status_copy, form)}
        {_section_card('Access model', 'Form login dibuat sederhana dan berlabel penuh agar mudah dioperasikan baik dengan pointer maupun keyboard.', access_model)}
      </section>
    """


def _build_configuration_body(job: JobDefinition | None) -> str:
    if job is None:
        return """
          <section class='mt-8'>
            <section class='rounded-[2rem] border border-dashed border-slate-300 bg-white/85 p-8 text-base leading-8 text-slate-600 shadow-[0_20px_50px_rgba(15,23,42,0.08)] backdrop-blur'>
              Belum ada job definition. Buat satu job lewat API admin terlebih dahulu, lalu halaman ini akan menampilkan editor konfigurasi yang sesuai spec.
            </section>
          </section>
        """

    params = job.params_schema_json if isinstance(job.params_schema_json, dict) else {}
    batching = params.get("batching_strategy", {}) if isinstance(params.get("batching_strategy"), dict) else {}
    advanced_params = {
        key: value
        for key, value in params.items()
        if key not in {"base_url", "sql_lab_url", "source_data_path", "batching_strategy"}
    }

    form = f"""
      <form class='grid gap-5' data-config-form>
        <div class='grid gap-4 lg:grid-cols-2 2xl:grid-cols-4'>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Name</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='name' value='{_escape(job.name)}' />
          </label>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Execution mode</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='execution_mode' value='{_escape(job.execution_mode)}' />
          </label>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Merge keys</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='merge_keys' value='{_escape(", ".join(job.merge_key_columns_json))}' />
          </label>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Identity columns</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='identity_columns' value='{_escape(", ".join(job.identity_columns_json))}' />
          </label>
        </div>

        <div class='grid gap-4 lg:grid-cols-2 2xl:grid-cols-4'>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Superset base URL</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='base_url' value='{_escape(params.get("base_url", ""))}' />
          </label>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>SQL Lab URL</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='sql_lab_url' value='{_escape(params.get("sql_lab_url", ""))}' />
          </label>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Source data path</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='source_data_path' value='{_escape(params.get("source_data_path", ""))}' />
          </label>
          <label class='grid gap-2 text-sm font-semibold text-slate-700'>
            <span>Batching parameter</span>
            <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='batching_param' value='{_escape(batching.get("param", ""))}' />
          </label>
        </div>

        <label class='grid gap-2 text-sm font-semibold text-slate-700'>
          <span>Batching values</span>
          <input class='rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='text' name='batching_values' value='{_escape(", ".join(batching.get("values", [])))}' />
        </label>

        <label class='grid gap-2 text-sm font-semibold text-slate-700'>
          <span>SQL template</span>
          <textarea class='min-h-56 rounded-xl border border-slate-300 bg-white px-4 py-4 text-sm leading-7 text-slate-950 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' name='sql_template'>{_escape(job.sql_template or "")}</textarea>
        </label>

        <label class='grid gap-2 text-sm font-semibold text-slate-700'>
          <span>Advanced params JSON</span>
          <textarea class='min-h-44 rounded-xl border border-slate-300 bg-slate-950 px-4 py-4 text-sm leading-7 text-slate-100 shadow-sm outline-none transition focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' name='advanced_params_json'>{_json_text(advanced_params)}</textarea>
        </label>

        <div class='flex flex-wrap gap-3'>
          <button class='inline-flex items-center rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' type='submit'>Save configuration</button>
          <a class='inline-flex items-center rounded-full border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-900 transition hover:border-slate-400 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' href='/ui/run-control'>Open run control</a>
        </div>
      </form>
    """

    return f"""
      <section class='mt-8'>
        {_section_card('Job definition editor', 'Field di bawah ini tetap memetakan langsung ke shape JobDefinition dan params SQL Lab yang sudah ada di codebase, tetapi sekarang dibungkus dengan layout yang lebih modern dan lebih mudah dibaca.', form)}
      </section>
    """


def _build_run_control_body(selected_run_id: int | None) -> str:
    with Session(engine) as session:
        job = _latest_job_definition(session)
        snapshot = _latest_snapshot(session)
        latest = latest_runs_summary(session)
        runs_payload = list_runs(session, page=1, per_page=12)
        recent_runs = runs_payload["data"]
        if selected_run_id is None and recent_runs:
            selected_run_id = int(recent_runs[0]["run_id"])
        selected_run = get_run_detail(session, selected_run_id) if selected_run_id else None

    extraction_card = latest.get("extraction") or {"status": "idle", "completed_at": None, "run_id": None}
    anomaly_card = latest.get("anomaly") or {"status": "idle", "completed_at": None, "run_id": None}

    action_panel = f"""
      <div class='flex flex-wrap gap-3'>
        <button type='button' data-run-action data-endpoint='/api/v1/run-control/extraction' data-payload='{html.escape(json.dumps({"job_definition_id": job.id, "debug": False}, ensure_ascii=False), quote=True) if job and job.id is not None else "{}"}' {'disabled' if job is None else ''} class='inline-flex items-center rounded-full bg-emerald-700 px-5 py-3 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white'>Run extraction job</button>
        <button type='button' data-run-action data-endpoint='/api/v1/run-control/anomaly' data-payload='{html.escape(json.dumps({"dataset_snapshot_id": snapshot.id}, ensure_ascii=False), quote=True) if snapshot and snapshot.id is not None else "{}"}' {'disabled' if snapshot is None else ''} class='inline-flex items-center rounded-full bg-sky-700 px-5 py-3 text-sm font-semibold text-white transition hover:bg-sky-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white'>Run anomaly/findings</button>
      </div>
    """

    rows = "".join(
        f"""
          <tr class='border-b border-slate-200/80 last:border-b-0'>
            <td class='px-4 py-4'>
              <a class='inline-flex rounded-lg px-2 py-1 font-bold text-emerald-800 underline decoration-emerald-300 underline-offset-4 transition hover:text-emerald-900 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white' href='{_page_url('/ui/run-control', selected_run_id=item["run_id"])}'>#{item["run_id"]}</a>
            </td>
            <td class='px-4 py-4 text-sm font-semibold text-slate-800'>{_escape(item['run_type'])}</td>
            <td class='px-4 py-4'>{_status_badge(item['status'])}</td>
            <td class='px-4 py-4 text-sm text-slate-700'>{_escape(_format_timestamp(item['created_at']))}</td>
            <td class='px-4 py-4 text-sm text-slate-700'>{_escape(_format_timestamp(item['completed_at']))}</td>
          </tr>
        """
        for item in recent_runs
    ) or "<tr><td colspan='5' class='px-4 py-8 text-sm text-slate-600'>No runs yet.</td></tr>"

    table = f"""
      <div class='overflow-x-auto rounded-xl border border-slate-200/80'>
        <table class='min-w-full bg-white'>
          <caption class='sr-only'>Recent run history</caption>
          <thead class='bg-slate-50'>
            <tr>
              <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-bold uppercase tracking-[0.22em] text-slate-500'>Run</th>
              <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-bold uppercase tracking-[0.22em] text-slate-500'>Type</th>
              <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-bold uppercase tracking-[0.22em] text-slate-500'>Status</th>
              <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-bold uppercase tracking-[0.22em] text-slate-500'>Created</th>
              <th scope='col' class='px-4 py-3 text-left text-[0.72rem] font-bold uppercase tracking-[0.22em] text-slate-500'>Completed</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    """

    step_list = "".join(
        f"""
          <li class='flex items-start justify-between gap-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3'>
            <div>
              <p class='text-sm font-bold text-slate-900'>{_escape(step['step_type'])}</p>
              <p class='mt-1 text-xs text-slate-500'>Step execution state</p>
            </div>
            {_status_badge(step['status'])}
          </li>
        """
        for step in (selected_run["steps"] if selected_run else [])
    ) or "<li class='rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-600'>No steps yet.</li>"

    detail = (
        f"""
          <dl class='grid gap-5'>
            <div>
              <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Run ID</dt>
              <dd class='mt-2 text-base font-bold text-slate-900'>#{_escape(selected_run['run_id'])}</dd>
            </div>
            <div>
              <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Run type</dt>
              <dd class='mt-2'>{_status_badge(selected_run['run_type'])}</dd>
            </div>
            <div>
              <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Current status</dt>
              <dd class='mt-2'>{_status_badge(selected_run['status'])}</dd>
            </div>
            <div>
              <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Steps</dt>
              <dd class='mt-3'>
                <ul class='grid gap-3'>{step_list}</ul>
              </dd>
            </div>
            <div>
              <dt class='text-xs font-extrabold uppercase tracking-[0.22em] text-slate-500'>Outputs</dt>
              <dd class='mt-3 overflow-x-auto rounded-xl border border-slate-200 bg-slate-950 p-4 text-sm leading-7 text-slate-100'><pre>{_json_text(selected_run["outputs"])}</pre></dd>
            </div>
          </dl>
        """
        if selected_run
        else "<div class='rounded-xl border border-dashed border-slate-300 bg-slate-50 px-5 py-10 text-sm leading-7 text-slate-600'>Pilih satu run untuk melihat detail execution dan artifact.</div>"
    )

    metric_cards = "".join(
        [
            _metric_card("Latest extraction", extraction_card["status"], "Ringkasan status extraction terakhir."),
            _metric_card("Extraction completed", _format_timestamp(extraction_card["completed_at"]), "Timestamp completion terakhir untuk extraction."),
            _metric_card("Latest anomaly", anomaly_card["status"], "Ringkasan status anomaly/findings terakhir."),
            _metric_card("Anomaly completed", _format_timestamp(anomaly_card["completed_at"]), "Timestamp completion terakhir untuk anomaly/findings."),
        ]
    )

    return f"""
      <section class='mt-8 grid gap-6'>
        <section class='grid gap-4 sm:grid-cols-2 xl:grid-cols-4' aria-label='Run control metrics'>
          {metric_cards}
        </section>
        {_section_card('Run actions', 'Run Control menggabungkan trigger extraction, trigger anomaly/findings, dan monitor run dalam satu surface admin-only yang konsisten.', action_panel)}
        <section class='grid gap-6 xl:grid-cols-[minmax(0,1.08fr)_minmax(22rem,0.92fr)]'>
          {_section_card('Recent runs', 'Tabel riwayat run mempertahankan struktur yang mudah discan dan menyediakan link langsung ke detail run terpilih.', table)}
          {_section_card('Run detail', 'Detail run sekarang lebih terstruktur untuk membantu diagnosis status, step execution, dan keluaran artifact.', detail, 'xl:sticky xl:top-24 h-fit')}
        </section>
      </section>
    """


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ui/overview", status_code=303)


@router.get("/ui", include_in_schema=False)
def ui_root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ui/overview", status_code=303)


@router.get("/ui/overview", response_class=HTMLResponse)
def overview_page(request: Request) -> str:
    return _render_shell(
        request,
        title="Overview",
        eyebrow="Observe",
        active_page="overview",
        body=_build_overview_body(),
        accent_copy="Ringkasan ini memusatkan perhatian pada freshness dataset, waktu query anomaly terakhir, dan komposisi findings saat ini.",
    )


@router.get("/ui/findings", response_class=HTMLResponse)
def findings_page(
    request: Request,
    identity_key: str | None = None,
    nks: str | None = None,
    kode_kab: str | None = None,
    severity: str | None = None,
    review_state: str | None = None,
    selected: str | None = None,
) -> str:
    return _render_shell(
        request,
        title="Findings",
        eyebrow="Investigate",
        active_page="findings",
        body=_build_findings_body(identity_key, nks, kode_kab, severity, review_state, selected),
        accent_copy="Workspace ini dibuat untuk pencarian, penyaringan per NKS atau kode kabupaten, dan ekspor anomaly result tanpa mencampur kontrol operasional ke dalam flow viewer.",
    )


@router.get("/ui/login", response_class=HTMLResponse)
def login_page(request: Request) -> str:
    return _render_shell(
        request,
        title="Login",
        eyebrow="Elevate",
        active_page="login",
        body=_build_login_body(request),
        accent_copy="Admin login menambah surface kontrol tanpa mengubah fungsi viewer yang sudah terbuka bagi semua orang.",
    )


@router.get("/ui/configuration", response_class=HTMLResponse)
def configuration_page(request: Request):
    gate = _require_admin_page(request, "Configuration")
    if isinstance(gate, HTMLResponse):
        return gate

    with Session(engine) as session:
        job = _latest_job_definition(session)

    return _render_shell(
        request,
        title="Configuration",
        eyebrow="Control",
        active_page="configuration",
        body=_build_configuration_body(job),
        accent_copy="Konfigurasi dipetakan langsung ke shape JobDefinition dan parameter SQL Lab yang memang sudah ada di platform, tanpa abstraksi baru yang tidak perlu.",
    )


@router.get("/ui/run-control", response_class=HTMLResponse)
def run_control_page(request: Request, selected_run_id: int | None = None):
    gate = _require_admin_page(request, "Run Control")
    if isinstance(gate, HTMLResponse):
        return gate

    return _render_shell(
        request,
        title="Run Control",
        eyebrow="Operate",
        active_page="run-control",
        body=_build_run_control_body(selected_run_id),
        accent_copy="Semua trigger dan monitor run dikonsolidasikan di satu tempat agar garis pemisah antara observe dan control tetap tegas.",
    )


@router.get("/ui/identity-findings", response_class=HTMLResponse, include_in_schema=False)
def legacy_findings_page(
    request: Request,
    identity_key: str | None = None,
    nks: str | None = None,
    kode_kab: str | None = None,
    severity: str | None = None,
    review_state: str | None = None,
    selected: str | None = None,
):
    return findings_page(
        request=request,
        identity_key=identity_key,
        nks=nks,
        kode_kab=kode_kab,
        severity=severity,
        review_state=review_state,
        selected=selected,
    )


@router.get("/ui/runs", response_class=HTMLResponse, include_in_schema=False)
def legacy_runs_page(request: Request, selected_run_id: int | None = None):
    return run_control_page(request=request, selected_run_id=selected_run_id)
