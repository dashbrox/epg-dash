#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvtv_grab.py — Scraper de tvtv.us → guía XMLTV
================================================

Genera una guía de programación en formato XMLTV a partir del sitio tvtv.us
(lineup de cable, p. ej. USA-NY63086-X). Pensado para ejecutarse en GitHub
Actions varias veces al día, como parte del repositorio otherepg: publica
`custom-guides/tvtv-us.xml.gz`.

Estrategia auto-adaptativa (el sitio cambió su API JSON por HTML/HTMX detrás
de Cloudflare, así que nada de endpoints estáticos):

  1. Abre la página del lineup con un navegador real (Playwright) — esto
     supera Cloudflare — y extrae la lista de canales.
  2. Visita UNA página de canal capturando todo el tráfico de red para
     descubrir el endpoint que devuelve la parrilla (patrón conocido:
     /partial/source/...). Se guarda en data/endpoint.json para reutilizarlo.
  3. Para cada estación intenta la "vía rápida" (petición HTTP con las
     cookies del navegador); si falla, renderiza la página completa.
  4. Parsea la parrilla (clases .gridAiring / .grid-row del sitio) y produce
     xmltv.xml y xmltv.xml.gz.

Uso (desde la raíz del repo otherepg):
    python tvtv-us/tvtv_grab.py --out /tmp/t.xml --gz custom-guides/tvtv-us.xml.gz
    python tvtv-us/tvtv_grab.py --map-csv channels_map.csv     # ids compartidos
    python tvtv-us/tvtv_grab.py --days 5 --max-stations 10    # prueba rápida
    python tvtv-us/tvtv_grab.py --slow-budget 0               # render sin límite
    python tvtv-us/tvtv_grab.py --probe                       # diagnóstico de red

Integración con otherepg:
    Con --map-csv channels_map.csv, cada canal cuyo id de estación figure en
    la columna tvtv_site_id adopta el xmltv_id del CSV (el mismo que usan las
    guías de tvpassport / tvtv.ca / tvguide), lo que permite fusionar
    programas entre fuentes. Los demás canales conservan el id propio
    «{estación}-{número}.tvtv.us».

Variables de entorno:
    TVTV_LINEUP_URL   URL del lineup (por defecto USA-NY63086-X, NY 10080)
    TVTV_DAYS         días de guía (por defecto 3)
    TVTV_MAP_CSV      mapa opcional de ids (equivale a --map-csv)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr

from bs4 import BeautifulSoup, NavigableString
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

TZ_ET = ZoneInfo("America/New_York")
DEFAULT_LINEUP_URL = "https://www.tvtv.us/ny/new-york/10080/luUSA-NY63086-X"

STN_RE = re.compile(r"/stn(?P<stn_id>\d+)-(?P<num>[^-]+)-(?P<call>[^/?#]+)")
DAY_RE = re.compile(
    r"\b(?P<wd>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    r",?\s+(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)\.?,?\s+(?P<day>\d{1,2})\b",
    re.IGNORECASE,
)
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
TIME_RE = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>[AaPp])\.?[Mm]\.?")
EPISODE_RE = re.compile(r"\bS(\d{1,2})\s*[:\-·]?\s*E(\d{1,3})\b", re.IGNORECASE)
KEY_URL_RE = re.compile(r"partial|stn|grid|schedule|lineup|airing|api", re.IGNORECASE)
# Cabecera de día de la parrilla ("Sunday, Nov 1"). Con fullmatch: solo texto
# que SEA exactamente una fecha re-ancla el día (una sinopsis no colisiona).
ANCHOR_RE = re.compile(
    r"(?P<wd>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    r"|Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
    r",?\s+(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"
    r",?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\.?",
    re.IGNORECASE,
)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ_ET).strftime('%H:%M:%S')} ET] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Motor de navegador (Playwright) con captura de red
# ---------------------------------------------------------------------------

class Captured:
    __slots__ = ("url", "status", "ctype", "size", "body")

    def __init__(self, url, status, ctype, size, body):
        self.url, self.status, self.ctype, self.size, self.body = url, status, ctype, size, body


class Browser:
    """Contexto Playwright reutilizable que captura respuestas candidatas."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.captured: list[Captured] = []
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self) -> "Browser":
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        if sys.platform.startswith("linux"):
            args.append("--no-sandbox")
        try:  # Chrome real si está instalado (mejor huella que Chromium)
            self.browser = self.pw.chromium.launch(channel="chrome", headless=self.headless, args=args)
            log("Navegador: Google Chrome")
        except Exception:
            self.browser = self.pw.chromium.launch(headless=self.headless, args=args)
            log("Navegador: Chromium (Playwright)")
        self.context = self.browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        )
        self.context.add_init_script(STEALTH_JS)
        self.context.on("response", self._on_response)
        self.page = self.context.new_page()
        return self

    def close(self) -> None:
        for c in (self.context, self.browser):
            try:
                if c:
                    c.close()
            except Exception:
                pass
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

    # -- captura -------------------------------------------------------------

    def _on_response(self, resp) -> None:
        try:
            if len(self.captured) > 500:
                return
            url = resp.url
            if not KEY_URL_RE.search(url):
                return
            ctype = (resp.headers or {}).get("content-type", "")
            if not any(t in ctype for t in ("text/html", "text/plain", "application/json", "text/xml")):
                return
            body = resp.text()
            self.captured.append(Captured(url, resp.status, ctype, len(body), body or ""))
        except Exception:
            pass

    def clear_captured(self) -> None:
        self.captured.clear()

    # -- navegación / peticiones ---------------------------------------------

    def goto(self, url: str, wait_selector: str | None = None, timeout_ms: int = 30000):
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_selector:
            try:
                self.page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except Exception:
                pass
        return self.page

    def lineup_html(self) -> str:
        """innerHTML del contenedor #lineup (parrilla renderizada)."""
        el = self.page.query_selector("#lineup")
        return el.evaluate("el => el.innerHTML") if el else ""

    def api_get(self, url: str, timeout_ms: int = 20000) -> tuple[int, str]:
        """Petición HTTP con cookies y huella del navegador (vía rápida)."""
        r = self.context.request.get(url, timeout=timeout_ms, fail_on_status_code=False)
        try:
            body = r.text()
        except Exception:
            body = ""
        return r.status, body


# ---------------------------------------------------------------------------
# Canales
# ---------------------------------------------------------------------------

def load_channel_cache(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_channel_cache(path: Path, channels: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(channels, indent=1, ensure_ascii=False), encoding="utf-8")


def load_map_csv(path: Path | None) -> dict[str, dict]:
    """channels_map.csv del repo (el que usa build_custom_lists.py).

    Devuelve {tvtv_site_id: {xmltv_id, name}} para que los canales de esta
    guía adopten el mismo xmltv_id que las demás guías custom y los programas
    puedan fusionarse después. Se basa en que tvtv.us y tvtv.ca comparten el
    espacio de ids de estación (Gracenote/TMS).
    """
    if not path or not path.exists():
        if path:
            log(f"AVISO: mapa CSV no encontrado ({path}); se usan ids propios")
        return {}
    mapa: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sid = (row.get("tvtv_site_id") or "").strip()
            xid = (row.get("xmltv_id") or "").strip()
            if not sid or not xid or sid in mapa:
                continue
            mapa[sid] = {"xmltv_id": xid, "name": (row.get("name") or "").strip()}
    return mapa


def fetch_channels(bw: Browser, lineup_url: str, cache_path: Path,
                   persist: bool = True) -> tuple[list[dict], str]:
    """Extrae los canales del DOM del lineup; si falla, usa la caché local.

    Con persist=False (fixture local) no se lee ni se escribe la caché: así una
    prueba local nunca contamina la lista real de 562 canales del repo.
    """
    channels: list[dict] = []
    try:
        page = bw.goto(lineup_url, wait_selector="a[href*='/stn']", timeout_ms=45000)
        page.wait_for_timeout(1200)
        hrefs = page.eval_on_selector_all(
            "a[href*='/stn']", "els => els.map(e => e.getAttribute('href'))")
        seen: set[tuple[str, str]] = set()
        for h in hrefs or []:
            m = STN_RE.search(h or "")
            if not m:
                continue
            key = (m.group("stn_id"), m.group("num"))
            if key in seen:
                continue
            seen.add(key)
            channels.append({"stn_id": m.group("stn_id"), "num": m.group("num"),
                             "call": m.group("call")})
        if channels:
            log(f"Lineup: {len(channels)} canales extraídos del DOM")
            if persist:
                save_channel_cache(cache_path, channels)
    except Exception as e:
        log(f"AVISO: lineup no disponible en DOM ({e})")
    if not channels and persist:
        channels = load_channel_cache(cache_path)
        log(f"Lineup desde caché local: {len(channels)} canales")
    if not channels:
        raise RuntimeError("Sin canales: falló el DOM y no hay caché local")

    lineup_id = "USA-NY63086-X"
    m = re.search(r"lu([A-Za-z]{2,3}-[A-Za-z0-9]+-[A-Za-z0-9]+)", lineup_url)
    if m:
        lineup_id = m.group(1)
    try:
        attr = bw.page.eval_on_selector("#lineup", "el => el.dataset.lineup")
        if attr:
            lineup_id = attr
    except Exception:
        pass
    return channels, lineup_id


# ---------------------------------------------------------------------------
# Descubrimiento del endpoint de datos
# ---------------------------------------------------------------------------

def looks_like_grid(body: str) -> bool:
    if not body:
        return False
    if "gridAiring" in body or "grid-row" in body:
        return True
    return bool(DAY_RE.search(body[:4000])) and "airing" in body.lower()


def discover_endpoint(bw: Browser, lineup_url: str, first_stn: dict,
                      debug_dir: Path) -> tuple[str | None, str]:
    """Abre una página de canal con captura de red y deduce el endpoint.

    Devuelve (template | None, html_renderizado). El template lleva {stn}
    donde va el id de estación.
    """
    slug = f"stn{first_stn['stn_id']}-{first_stn['num']}-{first_stn['call']}"
    url = f"{lineup_url.rstrip('/')}/{slug}"
    log(f"Descubrimiento de endpoint con {slug} …")
    bw.clear_captured()
    try:
        bw.goto(url, wait_selector=".gridAiring", timeout_ms=35000)
        bw.page.wait_for_timeout(2500)
    except Exception as e:
        log(f"AVISO durante descubrimiento: {e}")

    debug_dir.mkdir(parents=True, exist_ok=True)
    with open(debug_dir / "probe_network.log", "w", encoding="utf-8") as fh:
        for c in bw.captured:
            fh.write(f"{c.status} {c.ctype} {c.size}B  {c.url}\n  "
                     f"{c.body[:300].strip()!r}\n\n")

    grid_html = ""
    try:
        grid_html = bw.lineup_html()
    except Exception:
        pass

    template: str | None = None
    for c in reversed(bw.captured):
        if c.status == 200 and looks_like_grid(c.body):
            if not grid_html:
                grid_html = c.body
            if "/partial/" in c.url:
                template = c.url.replace(first_stn["stn_id"], "{stn}") \
                    if first_stn["stn_id"] in c.url else c.url
                log(f"Endpoint de parrilla detectado: {c.url}")
            break

    if template is None:  # heurística: alguna llamada /partial/source/...
        for c in bw.captured:
            m = re.search(r"/partial/[^\"'\s]+", c.url)
            if m:
                template = m.group(0)
                log(f"Posible endpoint (sin validar): {template}")
                break
    return template, grid_html


def validate_fast_path(bw: Browser, base: str, lineup_id: str, stn: dict,
                       template: str, first_stn: dict) -> tuple[str | None, str]:
    """Valida la vía rápida con una segunda estación. Devuelve (template, html)."""
    stn_id, num, call = stn["stn_id"], stn["num"], stn["call"]
    slug = f"stn{stn_id}-{num}-{call}"
    old_slug = f"stn{first_stn['stn_id']}-{first_stn['num']}-{first_stn['call']}"
    cands: list[str] = []
    if "{stn}" in template:
        full = template.format(stn=stn_id, slug=slug, num=num, call=call, lineup=lineup_id)
        # el template suele venir de la captura de red ⇒ ya es absoluto
        cands.append(full if re.match(r"^https?://", full) else base.rstrip("/") + full)
    if first_stn["stn_id"] in template:
        cands.append(template.replace(first_stn["stn_id"], stn_id))
        cands.append(template.replace(old_slug, slug))
    seen: set[str] = set()
    for url in [c for c in cands if c and not (c in seen or seen.add(c))]:
        try:
            status, body = bw.api_get(url)
        except Exception:
            continue
        if status == 200 and looks_like_grid(body):
            log(f"Vía rápida validada: {url}")
            return url.replace(stn_id, "{stn}"), body
    return None, ""


# ---------------------------------------------------------------------------
# Parseo de la parrilla
# ---------------------------------------------------------------------------

def _month_from(mo: str) -> int:
    return MONTHS[mo[:3].lower()]


def _pick_year(month: int, day: int, today: datetime) -> int:
    best, best_delta = today.year, 10 ** 9
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = datetime(y, month, day)
        except ValueError:
            continue
        delta = abs((d.date() - today.date()).days)
        if delta < best_delta:
            best, best_delta = y, delta
    return best


def _parse_day_header(m: re.Match, today: datetime) -> datetime:
    month, day = _month_from(m.group("mon")), int(m.group("day"))
    return datetime(_pick_year(month, day, today), month, day, tzinfo=TZ_ET)


def _parse_airing(el) -> dict | None:
    """Extrae hora, título, descripción y episodio de un nodo .gridAiring."""
    text = el.get_text(" ", strip=True)
    if not text:
        return None
    tm = TIME_RE.search(text)
    title, desc, episode = "", "", ""

    for c in el.select('[class*="title"]'):
        t = c.get_text(" ", strip=True)
        if t and t != text:
            title = t
            break
    if not title:
        a = el.find("a")
        if a is not None:
            title = a.get_text(" ", strip=True)
    if not title:
        title = TIME_RE.sub("", text).strip(" ·|-")

    for c in el.select('[class*="desc"], [class*="episode"], [class*="sub"]'):
        t = c.get_text(" ", strip=True)
        if t and t != title and t != text and not desc:
            desc = t

    ep = EPISODE_RE.search(text)
    if ep:
        episode = f"S{int(ep.group(1)):0d}E{int(ep.group(2)):02d}"
    if not tm or not title:
        return None
    h, mnt, ap = int(tm.group("h")), int(tm.group("m")), tm.group("ap").upper()
    return {"hh": h % 12 + (12 if ap == "P" else 0), "mm": mnt,
            "title": title.strip(), "desc": desc.strip(), "episode": episode}


def parse_grid(html: str, today_et: datetime) -> tuple[list[dict], bool]:
    """HTML de parrilla → lista de programas (start/title/desc/episode).

    Recorre el DOM en orden de documento: cada cabecera de día ("Sunday,
    Nov 1") fija la fecha para las emisiones .gridAiring siguientes. Devuelve
    (programas, ok); ok=False si no se pudo anclar ninguna fecha.
    """
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("#lineup") or soup

    progs: list[dict] = []
    current_day: datetime | None = None
    orphan_airings: list[dict] = []
    n_headers = 0

    for node in root.descendants:
        if isinstance(node, NavigableString):
            m = ANCHOR_RE.fullmatch(str(node).strip())
            if m:
                current_day = _parse_day_header(m, today_et)
                n_headers += 1
            continue
        classes = node.get("class") or []
        if "gridAiring" in classes:
            d = _parse_airing(node)
            if not d:
                continue
            if current_day is None:
                orphan_airings.append(d)
            else:
                start = current_day.replace(hour=d["hh"], minute=d["mm"],
                                            second=0, microsecond=0)
                progs.append({"start": start, "title": d["title"],
                              "desc": d["desc"], "episode": d["episode"]})

    # Emisiones sin cabecera previa: se anclan continuando la última secuencia
    if orphan_airings and progs:
        base = progs[-1]["start"]
        for d in orphan_airings:
            base = base + timedelta(minutes=30)
            progs.append({"start": base, "title": d["title"],
                          "desc": d["desc"], "episode": d["episode"]})
    elif orphan_airings and not progs:
        return [], False  # sin fechas anclables → el parser necesita cabeceras

    if not n_headers or not progs:
        return [], False

    # En orden de documento una jornada cruza medianoche (10:30 PM → 12:00 AM):
    # una hora MENOR que la anterior ⇒ +1 día. Debe evaluarse antes de ordenar,
    # porque tras el sort esos slots quedarían al principio del día.
    prev = None
    for p in progs:
        if prev is not None and p["start"] < prev:
            p["start"] = p["start"] + timedelta(days=1)
        prev = p["start"]
    progs.sort(key=lambda p: p["start"])
    return progs, True


def fill_stops(progs: list[dict], window_end: datetime) -> list[dict]:
    """Duración = hasta el inicio de la siguiente emisión; recorta la ventana."""
    progs.sort(key=lambda p: p["start"])
    out = []
    for i, p in enumerate(progs):
        nxt = progs[i + 1]["start"] if i + 1 < len(progs) else None
        stop = nxt or (p["start"] + timedelta(minutes=60))
        if stop <= p["start"]:
            stop = p["start"] + timedelta(minutes=30)
        if stop > window_end:
            stop = window_end
        p["stop"] = stop
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# XMLTV
# ---------------------------------------------------------------------------

def xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def _channel_id(ch: dict) -> str:
    """xmltv_id mapeado del repo o, si no, id propio «{estación}-{n}.tvtv.us»."""
    return ch.get("xmltv_id") or f"{ch['stn_id']}-{ch['num']}.tvtv.us"


def build_xmltv(channels: list[dict], programmes_by_stn: dict[str, list[dict]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
           '<tv generator-info-name="tvtv-grab (GitHub Actions)" '
           'source-info-url="https://www.tvtv.us/">']

    for ch in channels:
        cid = _channel_id(ch)
        out.append(f'  <channel id={quoteattr(cid)}>')
        out.append(f'    <display-name lang="en">'
                   f'{escape(ch.get("map_name") or (ch["num"] + " " + ch["call"]))}</display-name>')
        if ch.get("icon"):
            out.append(f'    <icon src={quoteattr(ch["icon"])} />')
        out.append('  </channel>')

    total = 0
    for ch in channels:
        cid = _channel_id(ch)
        for p in programmes_by_stn.get(ch["stn_id"], []):
            out.append(
                f'  <programme start="{xmltv_time(p["start"])}" stop="{xmltv_time(p["stop"])}" '
                f'channel={quoteattr(cid)}>')
            out.append(f'    <title lang="en">{escape(p["title"])}</title>')
            if p.get("desc"):
                out.append(f'    <desc lang="en">{escape(p["desc"])}</desc>')
            if p.get("episode"):
                out.append(f'    <episode-num system="onscreen">{escape(p["episode"])}</episode-num>')
            out.append('  </programme>')
            total += 1
    out.append('</tv>')
    log(f"XMLTV: {len(channels)} canales, {total} programas")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="tvtv.us → XMLTV (GitHub Actions)")
    ap.add_argument("--lineup-url", default=os.environ.get("TVTV_LINEUP_URL", DEFAULT_LINEUP_URL))
    ap.add_argument("--days", type=int, default=int(os.environ.get("TVTV_DAYS", "3")))
    ap.add_argument("--out", default="xmltv.xml")
    ap.add_argument("--gz", default=None)
    ap.add_argument("--max-stations", type=int, default=0)
    ap.add_argument("--slow-budget", type=int, default=40,
                    help="máx. de estaciones por vía lenta (render); 0 = ilimitado")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--probe", action="store_true", help="solo diagnóstico de red")
    ap.add_argument("--map-csv", default=os.environ.get("TVTV_MAP_CSV"),
                    help="channels_map.csv del repo: los canales cuyo id de "
                         "estación figure en tvtv_site_id adoptan su xmltv_id")
    ap.add_argument("--debug-dir", default="debug")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    debug_dir = Path(args.debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parent
    cache_path = repo_root / "data" / "channels_cache.json"
    endpoint_path = repo_root / "data" / "endpoint.json"

    parsed = urlparse(args.lineup_url)
    # Sin esquema http(s) ⇒ ruta del fixture local (no persistir nada del fixture)
    is_fixture = not parsed.scheme
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else "http://127.0.0.1:8777"
    lineup_url = args.lineup_url if parsed.scheme else f"{base}{args.lineup_url}"

    today_et = datetime.now(TZ_ET)
    window_start = today_et.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=args.days)
    log(f"Ventana: {window_start:%Y-%m-%d} → {window_end:%Y-%m-%d} ET "
        f"({args.days} días) | sitio: {base}"
        + (" (fixture local)" if is_fixture else ""))

    bw = Browser(headless=not args.headed).start()
    failures: list[str] = []
    icons: dict[str, str] = {}
    try:
        # 1) canales ---------------------------------------------------------
        channels, lineup_id = fetch_channels(bw, lineup_url, cache_path,
                                             persist=not is_fixture)

        # 1b) mapeo de ids compartidos del repo (otherepg) --------------------
        mapa = load_map_csv(Path(args.map_csv) if args.map_csv else None)
        n_map = 0
        for ch in channels:
            m = mapa.get(ch["stn_id"])
            if m:
                ch["xmltv_id"] = m["xmltv_id"]
                ch["map_name"] = m["name"]
                n_map += 1
        if args.map_csv:
            log(f"Mapa {args.map_csv}: {n_map}/{len(channels)} canales adoptan "
                f"el xmltv_id compartido del repo")
        unique_stns: list[dict] = []
        seen: set[str] = set()
        for ch in channels:
            if ch["stn_id"] not in seen:
                seen.add(ch["stn_id"])
                unique_stns.append(ch)
        log(f"Estaciones únicas: {len(unique_stns)}")
        if args.max_stations:
            unique_stns = unique_stns[: args.max_stations]
            log(f"Limitado a {len(unique_stns)} estaciones (--max-stations)")

        if args.probe:
            template, grid_html = discover_endpoint(bw, lineup_url, unique_stns[0], debug_dir)
            log(f"PROBE: template={template!r} html={len(grid_html)}B")
            (debug_dir / "probe_grid.html").write_text(grid_html or "", encoding="utf-8")
            return 0

        # 2) estrategia de descarga ------------------------------------------
        programmes_by_stn: dict[str, list[dict]] = {}

        def keep(slug_stn: str, html: str) -> bool:
            progs, ok = parse_grid(html, today_et)
            if ok:
                programmes_by_stn[slug_stn] = progs
            return ok

        template: str | None = None
        cached = {}
        if endpoint_path.exists():
            try:
                cached = json.loads(endpoint_path.read_text(encoding="utf-8"))
            except Exception:
                cached = {}
        first = unique_stns[0]
        if cached.get("template"):
            t = cached["template"]
            url = (t if t.startswith("http") else base + t).format(
                stn=first["stn_id"],
                slug=f"stn{first['stn_id']}-{first['num']}-{first['call']}",
                num=first["num"], call=first["call"], lineup=lineup_id)
            try:
                status, body = bw.api_get(url)
                if status == 200 and looks_like_grid(body) and keep(first["stn_id"], body):
                    template = t
                    log(f"Endpoint en caché válido: {t}")
            except Exception:
                pass

        if template is None:
            t, grid_html = discover_endpoint(bw, lineup_url, first, debug_dir)
            if grid_html and keep(first["stn_id"], grid_html):
                log(f"Estación {first['call']}: guía vía render de navegador")
                icon = None
                try:
                    el = bw.page.query_selector("#station-icon")
                    icon = el.get_attribute("src") if el else None
                except Exception:
                    pass
                if icon:
                    icons[first["stn_id"]] = (icon if icon.startswith("http")
                                              else base + icon)
            if t:
                # validar con la 2ª estación antes de confiar en él
                if len(unique_stns) > 1:
                    t2, body2 = validate_fast_path(bw, base, lineup_id,
                                                   unique_stns[1], t, first)
                    if t2 and keep(unique_stns[1]["stn_id"], body2):
                        template = t2
                if template is None and t:
                    log("Endpoint detectado pero sin validar; se usará modo navegador")
            if not is_fixture:  # nunca cachear el endpoint del fixture local
                endpoint_path.parent.mkdir(parents=True, exist_ok=True)
                endpoint_path.write_text(json.dumps({"template": template,
                                                     "lineup": lineup_id}, indent=1),
                                         encoding="utf-8")

        # 3) resto de estaciones ----------------------------------------------
        rest = [s for s in unique_stns if s["stn_id"] not in programmes_by_stn]
        log(f"Descarga: {len(programmes_by_stn)} listas, {len(rest)} pendientes "
            f"(plantilla: {'sí' if template else 'no'})")
        t0 = time.time()
        done = len(unique_stns) - len(rest)
        slow_used = 0
        for stn in rest:
            slug = f"stn{stn['stn_id']}-{stn['num']}-{stn['call']}"
            html = ""
            try:
                if template:
                    url = (template if template.startswith("http") else base + template).format(
                        stn=stn["stn_id"], slug=slug, num=stn["num"],
                        call=stn["call"], lineup=lineup_id)
                    status, html = bw.api_get(url)
                    if status != 200 or not looks_like_grid(html):
                        html = ""
                    time.sleep(random.uniform(0.15, 0.35))  # cortesía con el servidor
                if not html:  # vía lenta: página completa renderizada
                    if args.slow_budget and slow_used >= args.slow_budget:
                        failures.append(f"{slug}: sin vía rápida y presupuesto de render "
                                        f"agotado (--slow-budget {args.slow_budget})")
                        done += 1
                        continue
                    slow_used += 1
                    page = bw.goto(f"{lineup_url.rstrip('/')}/{slug}",
                                   wait_selector=".gridAiring", timeout_ms=28000)
                    page.wait_for_timeout(400)
                    html = bw.lineup_html() or page.content()
                    if stn["stn_id"] not in icons:
                        el = page.query_selector("#station-icon")
                        src = el.get_attribute("src") if el else None
                        if src:
                            icons[stn["stn_id"]] = src if src.startswith("http") else base + src
                    time.sleep(random.uniform(0.8, 1.8))
                if not keep(stn["stn_id"], html):
                    failures.append(f"{slug}: parrilla vacía o sin días reconocidos")
            except Exception as e:
                failures.append(f"{slug}: {type(e).__name__}: {e}")
            done += 1
            if done % 25 == 0 or done == len(unique_stns):
                rate = done / max(time.time() - t0, 1)
                eta = (len(unique_stns) - done) / max(rate, 0.01)
                log(f"Progreso {done}/{len(unique_stns)} ({rate:.1f}/s, "
                    f"ETA {eta/60:.0f} min, fallos {len(failures)})")

        # 4) recorte de ventana y duraciones -----------------------------------
        for stn_id in list(programmes_by_stn):
            progs = fill_stops(programmes_by_stn[stn_id], window_end)
            progs = [p for p in progs if p["start"] < window_end and p["stop"] > window_start]
            if progs:
                programmes_by_stn[stn_id] = progs
            else:
                del programmes_by_stn[stn_id]

        # 5) XMLTV ---------------------------------------------------------------
        out_channels = channels if not args.max_stations else \
            [c for c in channels if c["stn_id"] in programmes_by_stn]
        for ch in out_channels:
            if ch["stn_id"] in icons:
                ch["icon"] = icons[ch["stn_id"]]
        xml = build_xmltv(out_channels, programmes_by_stn)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(xml, encoding="utf-8")
        gz_path = Path(args.gz) if args.gz else out_path.with_suffix(out_path.suffix + ".gz")
        with gzip.open(gz_path, "wb", compresslevel=9) as fh:
            fh.write(xml.encode("utf-8"))
        log(f"Escritos {out_path} ({len(xml)/1e6:.1f} MB) y {gz_path} "
            f"({gz_path.stat().st_size/1e6:.1f} MB)")

        # 6) informe ---------------------------------------------------------------
        (debug_dir / "resumen.txt").write_text(
            f"Fecha: {datetime.now(TZ_ET).isoformat()}\n"
            f"Lineup: {lineup_id}\nCanales: {len(out_channels)}\n"
            f"Estaciones con guía: {len(programmes_by_stn)}\n"
            f"Programas: {sum(len(v) for v in programmes_by_stn.values())}\n"
            f"Fallos: {len(failures)}\n" + "\n".join(failures[:300]),
            encoding="utf-8")
        if failures:
            log(f"AVISO: {len(failures)} estaciones fallaron (debug/resumen.txt)")

        if not programmes_by_stn:
            log("ERROR: no se obtuvo ninguna guía")
            return 2
        if len(failures) / max(len(unique_stns), 1) > 0.30:
            log("ERROR: >30% de estaciones fallaron; no conviene publicar")
            return 2
        return 0
    finally:
        try:
            bw.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
