# tvtv.us — Guía EPG del lineup Optimum NY 10080

Scraper de **tvtv.us** (lineup Optimum, Nueva York 10080, identificador
`USA-NY63086-X`, 562 canales) que genera un archivo **XMLTV** con 3 días de
programación en hora del este (`America/New_York`) y lo publica en
`custom-guides/tvtv-us.xml.gz`, igual que las demás guías custom del repo.

- **URL pública:** `https://raw.githubusercontent.com/dashbrox/otherepg/master/custom-guides/tvtv-us.xml.gz`
- **Frecuencia:** cada 6 horas (workflow «Guía tvtv.us (Optimum NY)», `.github/workflows/tvtv-us.yml`)
- **Complementa** a `custom-guides/tvtv.xml.gz` (esa es tvtv.**ca**; esta es tvtv.**us**)

## Por qué un scraper aparte

tvtv.us eliminó su API JSON pública en **julio de 2026** (el grabber JS de
`sites/tvtv.us` dejó de funcionar; ver `VERIFICACION_tvtv.md`). Hoy el sitio
sirve la programación como fragmentos HTML detrás de Cloudflare, así que esta
guía se genera con un scraper **auto-adaptativo** en Python + Playwright:

1. **Navegador real** abre el lineup y extrae los 562 canales del DOM
   (respaldo local: `data/channels_cache.json`).
2. **Descubre el endpoint** de la parrilla capturando el tráfico de red de una
   página de canal (patrón `/partial/source/…`); la plantilla se guarda en
   `data/endpoint.json` para reutilizarla.
3. **Vía rápida** (HTTP con cookies del navegador) para el resto de las
   estaciones, con pausas de cortesía; si una falla, render de página completa
   (hasta `--slow-budget` por corrida).
4. **Parseo** de la parrilla (`.gridAiring` + cabeceras de día) con detección
   del cruce de medianoche de la jornada de radiodifusión, y armado del
   XMLTV (el `stop` de cada programa es el `start` del siguiente).
5. Sale con código **2** si no hay guía o si falló >30 % de las estaciones:
   el workflow **no publica** una guía mala.

## Integración con las demás guías (xmltv_id compartido)

`build_custom_lists.py` mantiene un `xmltv_id` común en todas las fuentes
para poder fusionar programas. Este scraper participa en ese diseño con:

```bash
python tvtv-us/tvtv_grab.py --map-csv channels_map.csv ...
```

Cada canal cuyo id de estación aparezca en la columna `tvtv_site_id` del CSV
adopta ese `xmltv_id` (tvtv.us y tvtv.ca comparten el espacio de ids de
estación). **Hoy coinciden 37 de los 54 canales del CSV** (HBO, HBO Hits,
HBO Drama/Movies/Comedy, los Cinemax, los Starz/Encore, Bravo, FX, TCM, IFC,
MTV, Hallmark, Tennis, y las cadenas locales CBS/NBC/Fox/ABC/CW de NY).
El resto conserva el id propio `{estación}-{número}.tvtv.us`.

Para desactivarlo, quita `--map-csv channels_map.csv` del workflow.

## Depuración local (sin tocar el sitio real)

```bash
pip install -r tvtv-us/requirements.txt
python -m playwright install chromium

python tvtv-us/tests/fixture_site.py &     # imita el sitio en http://127.0.0.1:8777
python tvtv-us/tvtv_grab.py \
  --lineup-url /ny/new-york/10080/luUSA-NY63086-X \
  --out /tmp/prueba.xml --debug-dir /tmp/dbg   # sin «https://» apunta al fixture
```

El fixture imita lineup, shell de canal y endpoint `/partial/source/…`,
incluyendo cruce de medianoche y caracteres especiales (`&`, `<`, acentos).
El modo fixture es hermético: **no lee ni escribe** nada en `data/`, así que
probar en local nunca contamina la caché real de 562 canales.

Contra el sitio real, con pocas estaciones:

```bash
python tvtv-us/tvtv_grab.py --max-stations 10 --out /tmp/prueba.xml
```

Tras cada corrida, `--debug-dir` (en CI, artefacto `diagnostico-tvtv-us`)
contiene `resumen.txt` (canales, programas, fallos) y los volcados de red del
descubrimiento.

## Opciones

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--lineup-url` | `https://www.tvtv.us/ny/new-york/10080/luUSA-NY63086-X` | URL del lineup. Sin esquema `http(s)://` se interpreta como ruta del fixture local. Var. `TVTV_LINEUP_URL`. |
| `--days` | `3` | Días de guía. Var. `TVTV_DAYS`. |
| `--out` / `--gz` | `xmltv.xml` / `<out>.gz` | Archivos de salida. |
| `--map-csv` | — | `channels_map.csv`: los canales con `tvtv_site_id` coincidente adoptan su `xmltv_id`. Var. `TVTV_MAP_CSV`. |
| `--max-stations` | `0` (todos) | Limita la descarga a N estaciones (pruebas). |
| `--slow-budget` | `40` | Máximo de estaciones por render completo; `0` = sin límite. |
| `--probe` | — | Solo diagnóstico de red. |
| `--headed` | — | Muestra el navegador (depuración local). |
| `--debug-dir` | `debug` | Directorio de volcados de diagnóstico. |

## Estructura

```
tvtv-us/
├── tvtv_grab.py            # el scraper (autocontenido, sin tocar el resto del repo)
├── requirements.txt        # playwright, beautifulsoup4, lxml, tzdata
├── .gitignore              # excluye debug/
├── data/
│   ├── channels_raw.txt    # respaldo: rutas de los 562 canales del lineup
│   ├── channels_cache.json # lista en JSON (arranque si el lineup no abre)
│   └── endpoint.json       # plantilla del endpoint descubierto (lo escribe el scraper)
├── tests/fixture_site.py   # copia local del sitio para depurar
└── tools/regenerar_cache.py # rehace channels_cache.json desde el respaldo
```

`data/endpoint.json` y `data/channels_cache.json` se commitean cuando cambian
(el workflow lo hace) para que las corridas siguientes arranquen más rápido;
`tvtv-us/debug/` debe ir en `.gitignore`.

## Solución de problemas

- **El workflow falla con «>30% de estaciones fallaron» o «no se obtuvo
  ninguna guía»** — descarga el artefacto `diagnostico-tvtv-us`, mira
  `resumen.txt` y `probe_network.log`; el sitio cambió algo. Borra
  `data/endpoint.json` para forzar el redescubrimiento y prueba localmente
  con `--max-stations 5 --probe`.
- **Cloudflare da 403 desde tu IP** — normal en VPS/domicilios marcados;
  el scraper está pensado para ejecutarse en GitHub Actions. Si también
  falla ahí, prueba dejando solo `--slow-budget 0` y sube las pausas
  `random.uniform(…)` del bucle de descarga.
- **Faltan canales** — si el lineup cambió, la extracción del DOM lo corrige
  sola; para forzarlo desde el respaldo: `python tvtv-us/tools/regenerar_cache.py`.

## Uso razonable

El scraper imita a un navegador, hace una petición por estación con pausas
aleatorias y solo consulta lo que un usuario vería en la web. Los datos
pertenecen a tvtv.us; úsala para uso personal.
