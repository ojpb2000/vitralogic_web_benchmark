"""
social_fb_media_detect.py
─────────────────────────
Detecta si un post de Facebook con post_type=status es en realidad video o imagen,
inspeccionando la etiqueta og:type de la URL pública del post.

Además extrae el og:image de Facebook (thumbnail de alta calidad, mejor resolución
que el CDN de Rival IQ) para usarlo en el análisis multimodal con Gemini.

Cachea todos los resultados en data/.fb_media_type_cache.json para que requests
posteriores no repitan el fetch HTTP.
"""

from __future__ import annotations
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

CACHE_PATH = Path("data/.fb_media_type_cache.json")

# User-agent que Facebook usa para su propio crawler de previews (acepta contenido público)
FB_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

TIMEOUT_S = 12
DEFAULT_SLEEP_S = 0.3   # throttle entre requests para no saturar

_OG_PAT = re.compile(
    r'<meta\b[^>]+property=["\']og:([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']'
    r'|<meta\b[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:([^"\']+)["\']',
    re.IGNORECASE,
)


# ─── cache ────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.6, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": FB_UA})
    return s


def _parse_og_tags(html: str) -> dict[str, str]:
    """Extrae todos los meta og: del HTML."""
    meta: dict[str, str] = {}
    for m in _OG_PAT.finditer(html):
        if m.group(1):
            meta["og:" + m.group(1)] = m.group(2)
        else:
            meta["og:" + m.group(4)] = m.group(3)
    return meta


# ─── core detection ───────────────────────────────────────────────────────────

def detect_fb_post_media(
    url: str,
    *,
    cache: Optional[dict] = None,
    session: Optional[requests.Session] = None,
    sleep_s: float = DEFAULT_SLEEP_S,
) -> dict:
    """Inspecciona og:type de una URL de Facebook y devuelve un dict con:
        media_type  : "video" | "image" | "unknown"
        og_image    : URL de thumbnail de alta calidad desde CDN de Facebook (o None)
        raw_type    : valor crudo de og:type (ej. "video.other", "article")
        error       : presente si hubo excepción HTTP
        from_cache  : True si el resultado viene del caché
    """
    ck = _url_key(url)
    if cache is not None and ck in cache:
        return {**cache[ck], "from_cache": True}

    if sleep_s > 0:
        time.sleep(sleep_s)

    sess = session or _make_session()
    result: dict = {"media_type": "unknown", "og_image": None, "raw_type": None}

    try:
        resp = sess.get(url, timeout=TIMEOUT_S, allow_redirects=True)
        if resp.status_code == 200:
            og = _parse_og_tags(resp.text)
            raw = og.get("og:type", "")
            result["raw_type"] = raw
            t = raw.lower()
            if "video" in t:
                result["media_type"] = "video"
            elif t:
                # "article", "website", etc. → imagen
                result["media_type"] = "image"
            # Thumbnail de FB de alta calidad (cdn fbcdn.net)
            og_img = og.get("og:image", "")
            if og_img.startswith("http"):
                result["og_image"] = og_img
        else:
            result["error"] = f"http_{resp.status_code}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}:{str(exc)[:100]}"

    if cache is not None:
        # Guardamos el resultado sin "from_cache" para no contaminar el caché
        cache[ck] = {k: v for k, v in result.items() if k != "from_cache"}

    return result


# ─── DataFrame enricher ───────────────────────────────────────────────────────

def enrich_status_formats(df, *, verbose: bool = True) -> tuple:
    """Para posts de Facebook con post_type_raw='status' e image_url, detecta vía
    og:type si son video o imagen y actualiza 'format_normalized' en el DataFrame.

    También almacena el og:image de Facebook en la columna '_og_image' para que
    el clasificador Gemini lo use como thumbnail de alta calidad.

    Returns
    -------
    df_updated, stats_dict
    """
    cache   = _load_cache()
    session = _make_session()

    mask = (
        (df["platform"] == "facebook") &
        (df["post_type_raw"] == "status") &
        df["post_link"].notna() &
        (df["post_link"].str.strip() != "")
    )
    targets = df.index[mask]

    stats = {
        "total_status": int(mask.sum()),
        "checked": 0,
        "from_cache": 0,
        "video_detected": 0,
        "image_confirmed": 0,
        "unknown": 0,
        "errors": 0,
    }

    for idx in targets:
        url = str(df.at[idx, "post_link"]).strip()
        ck  = _url_key(url)
        is_cached = ck in cache

        res = detect_fb_post_media(
            url,
            cache=cache,
            session=session,
            sleep_s=0.0 if is_cached else DEFAULT_SLEEP_S,
        )
        stats["checked"] += 1
        if is_cached:
            stats["from_cache"] += 1

        mtype = res.get("media_type", "unknown")
        if mtype == "video":
            df.at[idx, "format_normalized"] = "video"
            stats["video_detected"] += 1
        elif mtype == "image":
            stats["image_confirmed"] += 1
        else:
            stats["unknown"] += 1

        if res.get("error"):
            stats["errors"] += 1

        # Almacenar el thumbnail de alta calidad de FB (puede ser None)
        og_img = res.get("og_image")
        if og_img:
            df.at[idx, "_og_image"] = og_img

        # Guardar caché cada 25 posts
        if stats["checked"] % 25 == 0:
            _save_cache(cache)
            if verbose:
                pct = int(stats["checked"] / stats["total_status"] * 100)
                print(
                    f"  og:type detect: {stats['checked']}/{stats['total_status']} "
                    f"({pct}%) | videos={stats['video_detected']} cache={stats['from_cache']}"
                )

    _save_cache(cache)

    if verbose:
        print(
            f"og:type detection completa: "
            f"videos={stats['video_detected']} | "
            f"imagen={stats['image_confirmed']} | "
            f"desconocido={stats['unknown']} | "
            f"desde_caché={stats['from_cache']} | "
            f"errores={stats['errors']}"
        )

    return df, stats
