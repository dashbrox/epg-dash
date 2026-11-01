#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fixture_site.py — Imitación local de la arquitectura de tvtv.us para probar
tvtv_grab.py sin tocar el sitio real.

Arquitectura imitada (la del sitio real desde julio 2026):
  * /ny/new-york/10080/luUSA-NY63086-X            → página del lineup (enlaces /stn...)
  * /ny/new-york/10080/luUSA-NY63086-X/stn...     → shell del canal; su JS llama al partial
  * /partial/source/USA-NY63086-X/{stn}           → fragmento HTML de la parrilla

Uso:  python tests/fixture_site.py   (puerto 8777)
"""
import html
import re
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
PORT = 8777
LINEUP_PATH = "/ny/new-york/10080/luUSA-NY63086-X"
LINEUP_ID = "USA-NY63086-X"

CHANNELS = [
    ("11331", "2", "WCBS"),
    ("11705", "4", "WNBC"),
    ("10035", "105", "AETV"),
    ("10179", "363", "ESPN"),
    ("10240", "400", "HBO"),
]

SHOWS = {
    "11331": ["CBS News New York", "Face the Nation", "NFL Football", "60 Minutes",
              "The Equalizer", "Blue Bloods"],
    "11705": ["NBC Nightly News", "Sunday Night Football", "The Tonight Show",
              "Saturday Night Live", "Meet the Press", "Law & Order"],
    "10035": ["Live PD Presents", "Court Cam", "The First 48", "Interrogation Raw",
              "Las Reinas de la TV", "Americas Most Wanted"],
    "10179": ["SportsCenter", "Monday Night Football", "NBA Countdown",
              "College Football Live", "Pardon the Interruption", "Baseball Tonight"],
    "10240": ["The Last of Us", "Curb Your Enthusiasm", "Real Time with Bill Maher",
              "Westworld & < Robots >", "Game of Thrones: S3E5", "Succession"],
}

BASE_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<header id="appbar"><h1 class="uqLZehC3">{heading}</h1></header>
{body}
<footer>Version 2-0-62</footer>
</body></html>"""


def lineup_page() -> str:
    links = "".join(
        f'<a href="./lu{LINEUP_ID}/stn{sid}-{num}-{call}">{num}</a>\n'
        for sid, num, call in CHANNELS
    )
    body = (f'<div id="lineup" data-lineup="{LINEUP_ID}" data-state="0">\n{links}</div>')
    return BASE_PAGE.format(title="TV Listings | TVTV.us", heading="New York 10080",
                            body=body)


def station_page(stn_id: str, num: str, call: str) -> str:
    js = f"""
    <div id="lineup" data-lineup="{LINEUP_ID}" data-state="0"></div>
    <img id="station-icon" src="/api/v2/gn/si/assets/s{int(stn_id):05d}_ld_h15_ab.png">
    <script>
      fetch('/partial/source/{LINEUP_ID}/{stn_id}')
        .then(r => r.text())
        .then(h => {{ document.getElementById('lineup').innerHTML = h;
                      document.getElementById('lineup').dataset.state = '1'; }});
    </script>"""
    return BASE_PAGE.format(title=f"{num} {call}", heading=f"{num}&thinsp;{call}", body=js)


def grid_fragment(stn_id: str) -> str:
    """Fragmento HTML de 3 días con la clase .gridAiring (como el sitio real).

    Cada día imita una jornada de radiodifusión: 6:00 AM → 4:30 AM del día
    siguiente (franjas de 90 min), de modo que el scraper debe detectar el
    cruce de medianoche (10:30 PM → 12:00 AM) y sumar un día. Los títulos se
    insertan con html.escape(): así «Westworld & < Robots >» llega al parser
    como entidades y el XMLTV de salida debe re-escaparlas.
    """
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    shows = SHOWS.get(stn_id, SHOWS["11331"])
    parts = []
    for d in range(3):
        day = today + timedelta(days=d)
        header = day.strftime("%A, %b ") + str(day.day)
        rows = []
        minutes = 6 * 60  # la jornada empieza a las 6:00 AM
        for i in range(16):  # 16 × 90 min = 24 h
            hh24, mm = divmod(minutes, 60)
            hh24 %= 24
            ampm = "AM" if hh24 < 12 else "PM"
            h12 = hh24 if 1 <= hh24 <= 12 else (hh24 - 12) or 12
            show = shows[i % len(shows)]
            rows.append(
                f'<div class="grid-row"><div class="gridAiring">'
                f'<span class="time">{h12}:{mm:02d} {ampm}</span>'
                f'<a class="airing-title" href="#">{html.escape(show)}</a>'
                f'<div class="airing-desc">Episodio con acentos: información &amp; detalles &lt;prueba&gt;</div>'
                f'</div></div>')
            minutes += 90
        parts.append(f'<div class="gridDay"><div class="date">{header}</div>{"".join(rows)}</div>')
    return "\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if path == LINEUP_PATH:
            return self._send(200, lineup_page().encode())
        m = re.search(rf"^{LINEUP_PATH}/stn(\d+)-([^-]+)-([^-]+)", path)
        if m:
            return self._send(200, station_page(*m.groups()).encode())
        if path.startswith(f"/partial/source/{LINEUP_ID}/"):
            stn_id = path.rsplit("/", 1)[-1]
            if any(stn_id == c[0] for c in CHANNELS):
                return self._send(200, grid_fragment(stn_id).encode())
            return self._send(404, b"Not found")
        if path.endswith(".png"):
            return self._send(200, b"\x89PNG\r\n\x1a\n", "image/png")
        return self._send(404, b"Cannot GET " + path.encode())

    def log_message(self, fmt, *args):  # silencio
        pass


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Fixture de tvtv.us en http://127.0.0.1:{PORT}{LINEUP_PATH}")
    srv.serve_forever()
