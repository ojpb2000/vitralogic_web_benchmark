"""
Clasificación multimodal con Gemini: elige una hoja de la taxonomía (macro/categoría/subcategoría)
+ intención de embudo, usando texto del post y opcionalmente imagen (URL o captura Playwright).

Caché: data/.social_gemini_classify_cache.json
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GEMINI_CACHE_PATH = ROOT / "data" / ".social_gemini_classify_cache.json"

MULTIMODAL_FORMATS = frozenset({"image", "video", "reel", "carousel"})


def _load_taxonomy_bundle():
    import importlib.util

    path = ROOT / "scripts" / "build_social_benchmark.py"
    spec = importlib.util.spec_from_file_location("_bsb_tax_gemini", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod.TAXONOMY_LEAVES, getattr(mod, "TAXONOMY_REVISION", "1")


def _load_taxonomy_leaves():
    return _load_taxonomy_bundle()[0]


def _taxonomy_json(leaves: list) -> str:
    rows = [
        {
            "macro_id": t[0],
            "macro_label": t[1],
            "category_id": t[2],
            "category_label": t[3],
            "subcategory_id": t[4],
            "subcategory_label": t[5],
        }
        for t in leaves
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _leaf_map(leaves: list) -> dict[tuple[str, str, str], tuple[str, str, str]]:
    return {(t[0], t[2], t[4]): (t[1], t[3], t[5]) for t in leaves}


def _valid_triples(leaves: list) -> set[tuple[str, str, str]]:
    return {(t[0], t[2], t[4]) for t in leaves}


def _cache_load() -> dict:
    if not GEMINI_CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(GEMINI_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _cache_save(data: dict) -> None:
    GEMINI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEMINI_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_key(row: dict, taxonomy_revision: str) -> str:
    parts = [
        str(taxonomy_revision),
        str(row.get("post_link") or ""),
        str(row.get("message") or "")[:400],
        str(row.get("format_normalized") or ""),
        str(row.get("platform") or ""),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]


def _parse_json_response(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _build_user_text_bundle(row: Any) -> str:
    """row: Series o dict con message, link_title, link_description, platform, company_canonical, format, post_link."""
    parts = [
        f"Plataforma: {row.get('platform', '')}",
        f"Marca (export): {row.get('company_canonical', '')}",
        f"Formato: {row.get('format_normalized', '')}",
        f"URL del post: {row.get('post_link', '')}",
        "--- Texto publicación ---",
        str(row.get("message") or "").strip(),
    ]
    lt = row.get("link_title")
    if lt and str(lt).strip():
        parts.append(f"Título de enlace: {lt}")
    ld = row.get("link_description")
    if ld and str(ld).strip():
        parts.append(f"Descripción de enlace: {ld}")
    return "\n".join(parts)


SYSTEM_INSTRUCTION = """Eres experto en marketing B2B/B2C para ventanería, vidrio arquitectónico, cancelería, PVC y aluminio en México y Latinoamérica.
Tu tarea es clasificar el post en EXACTAMENTE UNA hoja de la taxonomía (macro_id + category_id + subcategory_id copiados literalmente de la lista).
Prioriza el tema CENTRAL del post (texto e imagen), no hashtags sueltos ni menciones genéricas a "equipo" o "marca" si el foco real es producto, obra, promoción o educación.
Sé preciso: si hay varios temas, elige la hoja que mejor represente el mensaje principal."""


DISAMBIGUATION_GUIDE = """
Reglas de desempate (aplicar en este orden antes de elegir hoja):

1. HURACANES vs CERTIFICACIONES
   - Huracanes, ciclones, resistencia al viento/impacto, vidrio antihuracán, temporada de huracanes, tormenta → "huracanes_resiliencia_climatica" (educativo).
   - Salvo que el post sea predominantemente sobre ensayos de laboratorio (ASTM, NAMI, large/small missile, protocolo de impacto) → "certificaciones_nami" (prueba_confianza).

2. AISLAMIENTO TÉRMICO vs ACÚSTICO vs AMBOS
   - Ruido, sonido exterior, insonorización, decibeles, STC como foco → "aislamiento_acustico".
   - Calor/frío, factor solar, valor U, puente térmico, confort térmico como foco → "aislamiento_termico".
   - El post enfatiza EXPLÍCITAMENTE ambos (frases: "térmico y acústico", "termoacústico", "thermo-acoustic") → "aislamiento_termico_acustico".

3. SUSTENTABILIDAD vs EFICIENCIA ENERGÉTICA
   - Post sobre impacto ambiental, huella de carbono, reciclaje, construcción verde como mensaje central → "sustentabilidad".
   - Post sobre ahorro en factura, reducir consumo AC, LEED, net zero, energía → "eficiencia_energetica".

4. PROYECTOS INSPIRACIONALES vs PRUEBA/CONFIANZA
   - Fachada/espacio mostrado como inspiración estética, diseño, sin datos de obra ni cliente → inspiracional.
   - Avance de obra en curso, progreso documentado → "obra_avance" (prueba_confianza).
   - Proyecto ya terminado mostrado como caso real con cliente o desarrollador → "proyectos_documentados" o "proyecto_general".
   - Post sobre la planta de fabricación, maquinaria, proceso productivo → "fabricacion_planta" (prueba_confianza).

5. COMERCIAL: PRODUCTO vs SHOWROOM vs PROPUESTA DE VALOR
   - Post cuyo foco es invitar a visitar el showroom / agendar cita → "showroom_general" (comercial).
   - Post que presenta producto específico (PVC, aluminio, herrajes, etc.) sin CTA showroom → subcategoría de catalogo_productos correspondiente.
   - Post sobre seguridad, antirrobo, vidrio de protección → "seguridad" (comercial).
   - Post sobre diseño a la medida, personalización, "soluciones integrales" como promesa → "diseno_personalizado" (comercial).

6. MARCA: SUBCATEGORÍAS
   - Post orgulloso de ser empresa mexicana, "hecho en México", talento nacional → "hecho_en_mexico".
   - Año nuevo, Navidad, Día del Padre, fechas patrias, saludo de temporada → "fechas_especiales".
   - Trayectoria, fundación, años de experiencia, legacy → "historia_marca".
   - Propuesta de valor, misión, "creemos en", "somos líderes" → "propuesta_valor".
   - Valores, cultura interna, filosofía, equipo → "cultura_general".
   - NO uses macro "marca" solo porque aparezca el nombre de la empresa; si hay producto, obra o CTA, prioriza la hoja correcta.

7. CATCH-ALL
   - Si el post no encaja con confianza en ninguna hoja, usa sin_clasificar (macro, category y subcategory = "sin_clasificar") y confidence ≤ 0.45.
"""


def _user_prompt(taxonomy_json: str) -> str:
    return f"""Taxonomía (elige exactamente UNA fila; copia los IDs sin cambiarlos):

{taxonomy_json}
{DISAMBIGUATION_GUIDE}
Intención (elige una):
- awareness: descubrimiento, marca, inspiración ligera sin empuje a compra inmediata.
- consideration: evaluación, proyectos, educación, prueba social, inspiración de obra.
- decision: CTA fuerte (cotizar, WhatsApp, llamar, visitar, promoción directa).

Responde SOLO con un objeto JSON válido (sin markdown) con estas claves:
"macro_id", "category_id", "subcategory_id", "intent", "confidence" (número 0 a 1), "rationale_es" (1-2 frases en español: qué señal del post llevó a la hoja).

Si el contenido no encaja en ninguna hoja, usa macro_id, category_id, subcategory_id exactamente "sin_clasificar" y confidence baja."""


def call_gemini_classify(
    api_key: str,
    model: str,
    taxonomy_json: str,
    text_bundle: str,
    image: Image.Image | None,
) -> tuple[dict[str, Any] | None, str | None]:
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(
        model_name=model,
        system_instruction=SYSTEM_INSTRUCTION,
    )
    parts: list = [_user_prompt(taxonomy_json), "\n--- Contenido a clasificar ---\n", text_bundle]
    if image is not None:
        parts.append("\n[Imagen o fotograma adjunto: úsala junto al texto para decidir la hoja.]\n")
        parts.append(image)

    cfg_json = genai.GenerationConfig(
        temperature=0.15,
        max_output_tokens=600,
        response_mime_type="application/json",
    )
    cfg_plain = genai.GenerationConfig(
        temperature=0.15,
        max_output_tokens=600,
    )
    try:
        resp = m.generate_content(parts, generation_config=cfg_json)
    except Exception:
        try:
            resp = m.generate_content(parts, generation_config=cfg_plain)
        except Exception as e2:
            return None, f"api:{type(e2).__name__}:{e2}"
    raw = ""
    try:
        raw = (resp.text or "").strip()
    except (ValueError, AttributeError):
        if resp.candidates:
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    raw += part.text
        raw = raw.strip()
    try:
        data = _parse_json_response(raw)
        if not data:
            return None, "parse_json"
        return data, None
    except Exception as e:
        return None, f"parse:{type(e).__name__}:{e}"


def _normalize_intent(x: str) -> str:
    s = (x or "").strip().lower()
    if s in ("awareness", "consideration", "decision"):
        return s
    return "consideration"


def _classification_dict_from_gemma(
    data: dict[str, Any],
    leaf_labels: dict,
    valid: set[tuple[str, str, str]],
) -> dict | None:
    mid = str(data.get("macro_id") or "").strip()
    cid = str(data.get("category_id") or "").strip()
    sid = str(data.get("subcategory_id") or "").strip()
    if (mid, cid, sid) not in valid:
        return None
    mlab, clab, slab = leaf_labels[(mid, cid, sid)]
    conf = data.get("confidence")
    try:
        cf = float(conf)
    except (TypeError, ValueError):
        cf = 0.5
    intent = _normalize_intent(str(data.get("intent") or "consideration"))
    return {
        "macro": {"id": mid, "label": mlab},
        "category": {"id": cid, "label": clab},
        "subcategory": {"id": sid, "label": slab},
        "intent": intent,
        "score": max(0, min(10, int(round(cf * 10)))),
        "candidates_top": [],
    }


def screenshot_post_playwright(post_url: str, timeout_ms: int = 45000) -> tuple[bytes | None, str | None]:
    if not post_url or not str(post_url).startswith("http"):
        return None, "url_invalida"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright_no_instalado"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = ctx.new_page()
                page.goto(post_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(4500)
                shot = page.screenshot(type="jpeg", quality=72, full_page=False)
            finally:
                browser.close()
        if not shot:
            return None, "screenshot_vacio"
        im = Image.open(io.BytesIO(shot))
        im = im.convert("RGB")
        im.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), None
    except Exception as e:
        return None, f"playwright:{type(e).__name__}"


def run_gemini_taxonomy_pass(
    df,
    *,
    api_key: str,
    model: str,
    mode: str = "doubtful",
    min_confidence: float = 0.72,
    max_api_calls: int = 2500,
    sleep_s: float = 0.12,
    fetch_playwright: bool = False,
    min_rule_score: int = 2,
) -> tuple[Any, dict]:
    """
    Clasifica posts con Gemini.

    mode:
      doubtful    — solo posts sin_clasificar por reglas (rápido/barato)
      low_conf    — sin_clasificar + posts cuyo score de reglas < min_rule_score
      rules_only  — todos los posts clasificados por reglas (no toca los ya hechos por Gemini)
      multimodal  — formatos imagen/video/reel/carousel + sin_clasificar
      all         — todos los posts (incluye los ya clasificados por Gemini)

    min_rule_score: score mínimo para no enviar a Gemini en modo low_conf (default=2).
    """
    from social_vision_enrichment import fetch_image_jpeg_bytes

    leaves, taxonomy_revision = _load_taxonomy_bundle()
    taxonomy_json = _taxonomy_json(leaves)
    leaf_labels = _leaf_map(leaves)
    valid = _valid_triples(leaves)
    # sin_clasificar como hoja sintética para el modelo
    valid.add(("sin_clasificar", "sin_clasificar", "sin_clasificar"))
    leaf_labels[("sin_clasificar", "sin_clasificar", "sin_clasificar")] = (
        "Sin clasificar",
        "Sin clasificar",
        "Sin clasificar",
    )

    stats = {
        "mode": mode,
        "min_rule_score": min_rule_score,
        "rows_eligible": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "accepted": 0,
        "rejected_low_confidence": 0,
        "rejected_invalid_leaf": 0,
        "rejected_api_error": 0,
        "media_csv_url": 0,
        "media_playwright": 0,
        "media_none": 0,
    }

    cache = _cache_load()
    df = df.copy()
    if "classification_source" not in df.columns:
        df["classification_source"] = "rules"
    if "classification_llm" not in df.columns:
        df["classification_llm"] = None

    mode = (mode or "doubtful").strip().lower()
    if mode not in ("doubtful", "low_conf", "rules_only", "multimodal", "all"):
        mode = "doubtful"

    calls = 0

    def eligible(row) -> bool:
        cl = row.get("classification") or {}
        mid = (cl.get("macro") or {}).get("id", "")
        score = int(cl.get("score") or 0)
        fmt = row.get("format_normalized") or ""
        src = row.get("classification_source") or "rules"
        if mode == "all":
            return True
        if mode == "rules_only":
            # Todos los clasificados por reglas (respeta los ya hechos por Gemini)
            return src == "rules"
        if mode == "doubtful":
            return mid == "sin_clasificar"
        if mode == "low_conf":
            # sin_clasificar O clasificación por reglas con score bajo
            return mid == "sin_clasificar" or (src == "rules" and score < min_rule_score)
        if mode == "multimodal":
            return fmt in MULTIMODAL_FORMATS or mid == "sin_clasificar"
        return False

    for idx in df.index:
        if calls >= max_api_calls:
            break
        row = df.loc[idx]
        if not eligible(row):
            continue
        stats["rows_eligible"] += 1

        row_dict = row.to_dict()
        ck = _cache_key(row_dict, taxonomy_revision)
        if ck in cache and cache[ck].get("classification"):
            meta = cache[ck].get("llm_meta") or {
                "confidence": cache[ck].get("confidence"),
                "rationale_es": cache[ck].get("rationale_es"),
                "media_source": None,
                "rules_classification": None,
            }
            df.at[idx, "classification"] = cache[ck]["classification"]
            df.at[idx, "classification_source"] = "gemini"
            df.at[idx, "classification_llm"] = {**meta, "from_cache": True, "model": model}
            stats["cache_hits"] += 1
            continue

        text_bundle = _build_user_text_bundle(row)
        post_link = str(row.get("post_link") or "").strip()
        # Preferir og:image de Facebook (HD, detectado por --detect-status-media)
        # sobre el thumbnail del CSV de Rival IQ, que es menor resolución.
        og_image  = row.get("_og_image")
        image_url = row.get("image_url")
        img_pil: Image.Image | None = None
        media_source = "none"

        # Intentar primero con og:image (thumbnail HD de Facebook)
        if og_image and isinstance(og_image, str) and og_image.startswith("http"):
            jpeg_b, _err = fetch_image_jpeg_bytes(
                og_image, referer=post_link if post_link else None, retries=2
            )
            if jpeg_b:
                try:
                    img_pil = Image.open(io.BytesIO(jpeg_b))
                    media_source = "fb_og_image"
                    stats["media_csv_url"] += 1
                except OSError:
                    img_pil = None

        # Fallback: thumbnail del CSV de Rival IQ
        if img_pil is None and image_url and isinstance(image_url, str) and image_url.startswith("http"):
            jpeg_b, _err = fetch_image_jpeg_bytes(
                image_url, referer=post_link if post_link else None, retries=2
            )
            if jpeg_b:
                try:
                    img_pil = Image.open(io.BytesIO(jpeg_b))
                    media_source = "csv_url"
                    stats["media_csv_url"] += 1
                except OSError:
                    img_pil = None

        if img_pil is None and fetch_playwright and post_link.startswith("http"):
            jpeg_b, _err = screenshot_post_playwright(post_link)
            if jpeg_b:
                try:
                    img_pil = Image.open(io.BytesIO(jpeg_b))
                    media_source = "playwright"
                    stats["media_playwright"] += 1
                except OSError:
                    pass

        if img_pil is None:
            stats["media_none"] += 1

        rules_before = row.get("classification")
        data, err = call_gemini_classify(api_key, model, taxonomy_json, text_bundle, img_pil)
        calls += 1
        stats["api_calls"] += 1
        time.sleep(sleep_s)

        if err or not data:
            stats["rejected_api_error"] += 1
            df.at[idx, "classification_llm"] = {
                "error": err or "sin_data",
                "model": model,
                "media_source": media_source,
            }
            continue

        try:
            conf = float(data.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0

        cl_out = _classification_dict_from_gemma(data, leaf_labels, valid)
        if cl_out is None:
            stats["rejected_invalid_leaf"] += 1
            df.at[idx, "classification_llm"] = {
                "raw": data,
                "model": model,
                "error": "invalid_leaf",
                "media_source": media_source,
            }
            continue

        rationale = str(data.get("rationale_es") or "")[:1200]

        if conf < min_confidence:
            stats["rejected_low_confidence"] += 1
            df.at[idx, "classification_llm"] = {
                "rules_classification": rules_before,
                "gemini_classification": cl_out,
                "confidence": conf,
                "rationale_es": rationale,
                "model": model,
                "media_source": media_source,
                "kept_rules": True,
            }
            continue

        llm_meta = {
            "confidence": conf,
            "rationale_es": rationale,
            "media_source": media_source,
            "rules_classification": rules_before,
        }
        cache[ck] = {"classification": cl_out, "llm_meta": llm_meta}
        _cache_save(cache)

        df.at[idx, "classification"] = cl_out
        df.at[idx, "classification_source"] = "gemini"
        df.at[idx, "classification_llm"] = {**llm_meta, "model": model}
        stats["accepted"] += 1

    _cache_save(cache)
    return df, stats
