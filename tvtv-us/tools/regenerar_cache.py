#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenera data/channels_cache.json a partir de data/channels_raw.txt.

channels_raw.txt contiene las rutas de los enlaces de canales extraídos de la
página del lineup (p. ej. «./luUSA-NY63086-X/stn11331-2-WCBS»). Es la copia de
seguridad por si la extracción en vivo fallara en el futuro: nunca hay que
volver a rastrear el sitio para recuperar la lista.

Uso (desde la raíz del repo):
    python tools/regenerar_cache.py
"""
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "data" / "channels_raw.txt"
OUT = RAIZ / "data" / "channels_cache.json"

pat = re.compile(r"/stn(\d+)-([^-]+)-([^-]+)\s*$")

channels = []
vistos = set()
for linea in RAW.read_text(encoding="utf-8").splitlines():
    linea = linea.strip()
    if not linea:
        continue
    m = pat.search(linea)
    if not m:
        print("SIN COINCIDENCIA:", linea)
        continue
    stn_id, num, call = m.groups()
    clave = (stn_id, num)
    if clave in vistos:
        continue
    vistos.add(clave)
    channels.append({"stn_id": stn_id, "num": num, "call": call})

OUT.write_text(json.dumps(channels, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"OK: {len(channels)} canales -> {OUT}")
