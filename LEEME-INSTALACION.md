# Instalación en tu repo `otherepg`

Este ZIP contiene **todo lo que hay que subir** a `github.com/dashbrox/otherepg`
para generar la guía XMLTV de tvtv.us (Optimum, Nueva York 10080, 562 canales)
cada 6 horas con GitHub Actions.

## Contenido

```
├── LEEME-INSTALACION.md          ← este archivo (NO lo subas al repo)
├── .github/
│   └── workflows/
│       └── tvtv-us.yml           ← workflow: cada 6 h + botón "Run workflow"
└── tvtv-us/                      ← carpeta del scraper (SÍ se sube completa)
    ├── tvtv_grab.py              ← el scraper (Python + Playwright)
    ├── requirements.txt          ← dependencias
    ├── README.md                 ← documentación completa
    ├── .gitignore                ← excluye debug/
    ├── data/
    │   ├── channels_raw.txt      ← respaldo: rutas de los 562 canales
    │   └── channels_cache.json   ← lista de canales en JSON
    ├── tests/fixture_site.py     ← copia local del sitio para depurar
    └── tools/regenerar_cache.py  ← rehace la caché desde el respaldo
```

## Cómo subirlo (desde la web de GitHub)

1. Entra a tu repo `dashbrox/otherepg`.
2. **Carpeta `tvtv-us/`**: botón **Add file → Upload files**, arrastra la
   carpeta `tvtv-us` completa y haz commit. (Con `git`: copia la carpeta a la
   raíz del repo y haz `git add tvtv-us && git commit && git push`.)
3. **Workflow**: navega a `.github/workflows/` (ya existe en tu repo), entra y
   repite **Add file → Upload files** con `tvtv-us.yml`. El archivo debe quedar
   exactamente en `.github/workflows/tvtv-us.yml`.
4. Pestaña **Actions**: aparecerá «Guía tvtv.us (Optimum NY)». Ábrelo y pulsa
   **Run workflow** para la primera corrida manual.

## Qué hace el workflow

- Corre a las **00:23, 06:23, 12:23 y 18:23 UTC** (cada 6 h) y bajo demanda.
- Abre tvtv.us con un navegador real (Playwright) para superar Cloudflare,
  descubre solo el endpoint de la parrilla y baja los **562 canales / 3 días**.
- Publica `custom-guides/tvtv-us.xml.gz` en la rama `master`:
  **https://raw.githubusercontent.com/dashbrox/otherepg/master/custom-guides/tvtv-us.xml.gz**
- Con `--map-csv channels_map.csv` los canales que ya están en tu mapa
  adoptan el `xmltv_id` compartido (37 de 54 hoy) para poder fusionar guías.
  Si algún día borras ese CSV, el scraper solo avisa y sigue con ids propios.
- Si no hay guía o falla >30 % de las estaciones, **termina con error y no
  publica** nada; los volcados quedan en el artefacto `diagnostico-tvtv-us`.
- La primera corrida tarda (~30–60 min: 562 estaciones); las siguientes usan
  la caché del endpoint y van mucho más rápido.

## Probado antes de empaquetar

✔ Scraper end-to-end contra una copia local del sitio (fixture): 5 canales,
220 programas, cruce de medianoche, acentos y escapes XML correctos.
✔ Integración `--map-csv`: los canales mapeados adoptan su `xmltv_id`.
✔ Sintaxis del workflow YAML, cron y rutas de archivos coherentes.

Para repetir la prueba en tu PC (no toca el sitio real):

```bash
pip install -r tvtv-us/requirements.txt && python -m playwright install chromium
python tvtv-us/tests/fixture_site.py &      # en otra terminal
python tvtv-us/tvtv_grab.py --lineup-url /ny/new-york/10080/luUSA-NY63086-X \
  --out /tmp/prueba.xml --debug-dir /tmp/dbg
```

Más detalles y solución de problemas: `tvtv-us/README.md`.
