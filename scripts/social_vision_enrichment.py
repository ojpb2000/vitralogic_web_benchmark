"""
Descarga la URL de la columna `image` del CSV y obtiene una descripción breve vía visión
(Gemini u OpenAI) para mejorar la clasificación por keywords cuando el texto no basta.

Requiere: pip install requests Pillow google-generativeai (Gemini) y/o openai (OpenAI)
Variables de entorno típicas (mejor en .env en la raíz del proyecto):
  GEMINI_API_KEY o GOOGLE_API_KEY  — Google AI Studio
  OPENAI_API_KEY                   — si usas proveedor openai

Caché local: data/.social_vision_cache.json (por hash de URL).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / ".social_vision_cache.json"
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
MAX_EDGE_PX = 1024
REQUEST_TIMEOUT = 25
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

VISION_PROMPT = """Eres analista de contenido para marcas de ventanas, vidrio arquitectónico, cancelería y fachadas.
Describe la imagen en español en un solo párrafo corto (máximo 80 palabras). Enfócate en:
- Tipo de espacio o edificio (casa, hotel, oficina, obra en proceso, showroom).
- Qué se ve del producto: ventanas, puertas corredizas, vidrio panorámico, fachada, marcos, aluminio, PVC, interior/exterior.
- Si hay texto visible en la imagen (promociones, logos, medidas), menciónalo brevemente.
- Si es video/reel (fotograma), indícalo si es obvio.
No inventes datos que no se vean. Sin saludos ni preámbulos."""


def _cache_load() -> dict:
    if not CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _cache_save(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _url_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:20]


def fetch_image_jpeg_bytes(
    url: str,
    referer: str | None = None,
    retries: int = 0,
) -> tuple[bytes | None, str | None]:
    """
    Descarga imagen, la normaliza a JPEG y devuelve (bytes_jpeg, error_message).
    referer: URL del post (mejora algunos CDN de Meta).
    retries: reintentos ante 403/429/5xx.
    """
    if not url or not str(url).strip().startswith("http"):
        return None, "url_invalida"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer and referer.startswith("http"):
        headers["Referer"] = referer

    last_err: str | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            r = requests.get(
                url.strip(),
                timeout=REQUEST_TIMEOUT,
                headers=headers,
                stream=True,
            )
            if r.status_code in (403, 429, 500, 502, 503) and attempt < retries:
                time.sleep(1.2 * (attempt + 1))
                continue
            r.raise_for_status()
            buf = io.BytesIO()
            n = 0
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                n += len(chunk)
                if n > MAX_DOWNLOAD_BYTES:
                    return None, "archivo_muy_grande"
                buf.write(chunk)
            buf.seek(0)
            im = Image.open(buf)
            im = im.convert("RGB")
            im.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue(), None
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            last_err = f"download:HTTPError:{code}"
            if code in (403, 429, 500, 502, 503) and attempt < retries:
                time.sleep(1.2 * (attempt + 1))
                continue
            return None, last_err
        except requests.RequestException as e:
            last_err = f"download:{type(e).__name__}"
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
                continue
            return None, last_err
        except OSError as e:
            return None, f"imagen:{type(e).__name__}"
    return None, last_err or "download:unknown"


def caption_with_openai(jpeg_bytes: bytes, api_key: str, model: str) -> tuple[str | None, str | None]:
    try:
        from openai import OpenAI
    except ImportError:
        return None, "paquete_openai_no_instalado"

    b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
    client = OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            max_tokens=220,
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        return (text if text else None), None
    except Exception as e:
        return None, f"api:{type(e).__name__}:{e}"


def caption_with_gemini(jpeg_bytes: bytes, api_key: str, model: str) -> tuple[str | None, str | None]:
    try:
        import google.generativeai as genai
    except ImportError:
        return None, "paquete_google_generativeai_no_instalado"

    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)
    im = Image.open(io.BytesIO(jpeg_bytes))
    try:
        resp = m.generate_content(
            [VISION_PROMPT, im],
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 256,
            },
        )
        if not getattr(resp, "candidates", None):
            return None, "api:sin_candidatos"
        try:
            text = (resp.text or "").strip()
        except (ValueError, AttributeError):
            text = ""
        if not text and resp.candidates:
            parts = []
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    parts.append(part.text)
            text = " ".join(parts).strip()
        return (text if text else None), None
    except Exception as e:
        return None, f"api:{type(e).__name__}:{e}"


def enrich_posts_dataframe(
    df,
    *,
    vision_provider: str = "gemini",
    api_key: str | None,
    model: str = "gemini-2.0-flash",
    max_calls: int = 400,
    only_sin_clasificar: bool = True,
    sleep_s: float = 0.15,
    build_classification_fn=None,
) -> tuple[object, dict]:
    """
    Añade columna classification_enrichment y reemplaza classification cuando aplica.
    vision_provider: "gemini" | "openai"
    """
    import pandas as pd

    if build_classification_fn is None:
        raise ValueError("build_classification_fn requerido")

    provider = (vision_provider or "gemini").strip().lower()
    if provider not in ("gemini", "openai"):
        provider = "gemini"

    stats = {
        "vision_enabled": bool(api_key),
        "vision_provider": provider,
        "posts_considered": 0,
        "posts_enriched": 0,
        "cache_hits": 0,
        "skipped_no_url": 0,
        "errors": 0,
        "error_reasons": {},
        "truncated_max_calls": False,
    }

    if not api_key:
        return df, stats

    cache = _cache_load()
    calls = 0
    df = df.copy()
    df["classification_enrichment"] = pd.Series([None] * len(df), index=df.index, dtype=object)

    def call_caption(jpeg_bytes: bytes) -> tuple[str | None, str | None]:
        if provider == "openai":
            return caption_with_openai(jpeg_bytes, api_key, model)
        return caption_with_gemini(jpeg_bytes, api_key, model)

    for idx in df.index:
        if calls >= max_calls:
            stats["truncated_max_calls"] = True
            break

        row = df.loc[idx]
        cl = row.get("classification") or {}
        macro = (cl.get("macro") or {}).get("id", "")
        if only_sin_clasificar and macro != "sin_clasificar":
            continue

        url = row.get("image_url")
        if not url or not isinstance(url, str) or not url.strip().startswith("http"):
            stats["skipped_no_url"] += 1
            continue

        stats["posts_considered"] += 1
        key = _url_key(url)
        caption = None
        err = None

        if key in cache and cache[key].get("caption"):
            caption = cache[key]["caption"]
            stats["cache_hits"] += 1
        else:
            post_link = str(row.get("post_link") or "").strip()
            ref = post_link if post_link.startswith("http") else None
            jpeg_bytes, err_dl = fetch_image_jpeg_bytes(url, referer=ref, retries=2)
            if err_dl:
                err = err_dl
            else:
                caption, err = call_caption(jpeg_bytes)
                calls += 1
                if caption:
                    cache[key] = {"caption": caption, "url_preview": url[:80], "provider": provider}
                    _cache_save(cache)
                time.sleep(sleep_s)

        base_text = str(row.get("_classify_text") or row.get("message") or "")

        if caption:
            merged = f"{base_text}\n[descripcion_imagen_ia]: {caption}"
            new_cl = build_classification_fn(merged)
            df.at[idx, "classification"] = new_cl
            df.at[idx, "classification_enrichment"] = {
                "basis": "text+vision",
                "vision_provider": provider,
                "vision_model": model,
                "vision_caption": caption[:2000],
                "cache_key": key,
            }
            stats["posts_enriched"] += 1
        else:
            reason = err or "sin_caption"
            stats["errors"] += 1
            stats["error_reasons"][reason] = stats["error_reasons"].get(reason, 0) + 1
            df.at[idx, "classification_enrichment"] = {
                "basis": "text",
                "vision_attempted": True,
                "vision_provider": provider,
                "vision_error": reason,
            }

    return df, stats
