"""
Clasifica exportaciones Rival IQ (Facebook + Instagram) y genera dashboard/social_data.json.

Fuentes esperadas en data/:
  - rivaliq_top_landscape_posts_aviglass_* facebook.csv
  - rivaliq_top_landscape_posts_aviglass_* instagram.csv
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_JSON = ROOT / "dashboard" / "social_data.json"

# Patrones de tema: (id, palabras_clave) — orden importa para desempate de tema primario
THEME_KEYWORDS: list[tuple[str, list[str]]] = [
    ("promocion_cta", [
        "cotización", "cotizacion", "whatsapp", "llámanos", "llamanos", "visítanos", "visitenos",
        "contáctanos", "contactanos", "contacto", "solicita", "showroom", "mándanos", "mandanos",
        "teléfono", "telefono", "llama al", "escríbenos", "escribenos", "reserva", "agenda",
    ]),
    ("proyecto_obra", [
        "proyecto terminado", "obra terminada", "otro proyecto", "instalación", "instalamos",
        "instalamos cancel", "proyecto más", "desarrollo", "edificio", "fachada",
    ]),
    ("certificacion_norma", [
        "nami", "certific", "norma", "estándar", "estandar", "2640", "huracán", "hurricane",
        "resistencia al viento", "ensayad", "estructural", "sellado",
    ]),
    ("premium_proyecto", [
        "ritz", "four seasons", "los cabos", "cancún", "hotel", "lujo", "luxury", "residencia",
        "premium", "vertical", "exclusiv", "horizonte house", "le blanc", "spa resort",
    ]),
    ("producto_tecnico", [
        "pvc", "termo-acústic", "termoacustic", "acústic", "acustic", "térmic", "termic",
        "aislamiento", "kömmerling", "kommerling", "premiline", "eurofutur", "eurofine",
        "perfil", "refuerzo", "cámara", "camara", "hermétic", "hermetic", "vidrio laminado",
        "doble acristalamiento", "seiciento", "avi45", "avi90", "sistema si", "sliding",
    ]),
    ("educativo_tip", [
        "¿", "sabías", "reduce", "hasta un", "%", "por qué", "porque el", "tip:", "dato",
        "did you know", "statistics", "estudio", "investigación",
    ]),
    ("sostenibilidad_energia", [
        "energía", "energia", "leed", "well", "sostenib", "eficiencia energética", "ac ",
        "climatización", "uv ", "huracán", "climate", "carbono", "ahorro",
    ]),
    ("lifestyle_comfort", [
        "confort", "silencio", "hogar", "bienestar", "luz natural", "tranquilidad", "peaceful",
        "retiro", "sanctuary", "views", "panoramic", "vista",
    ]),
    ("marca_story", [
        "#intothefuture", "intothefuture", "legacy", "visión", "vision", "avipartner",
        "partner", "network", "historia", "path", "innovation", "innovación",
    ]),
]


def strip_accents(s: str) -> str:
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c))


def normalize_for_match(text: str) -> str:
    return strip_accents(text.lower())


def score_themes(text: str) -> tuple[list[str], str]:
    """Devuelve (lista de temas con score>=1, tema primario)."""
    blob = normalize_for_match(text)
    scores: list[tuple[str, int]] = []
    for tid, kws in THEME_KEYWORDS:
        sc = sum(1 for kw in kws if normalize_for_match(kw) in blob)
        if sc > 0:
            scores.append((tid, sc))
    if not scores:
        return (["sin_clasificar"], "sin_clasificar")
    scores.sort(key=lambda x: (-x[1], next(i for i, (t, _) in enumerate(THEME_KEYWORDS) if t == x[0])))
    themes = [t for t, _ in scores]
    primary = scores[0][0]
    return (themes, primary)


HASHTAG_RE = re.compile(r"#([\w\u00c0-\u024f]+)", re.UNICODE)


def extract_hashtags(text: str) -> list[str]:
    if not text or not isinstance(text, str):
        return []
    return [m.group(1) for m in HASHTAG_RE.finditer(text)]


def normalize_format(platform: str, raw: str) -> str:
    r = (raw or "").strip().lower()
    if platform == "instagram":
        if r == "reel":
            return "reel"
        if r == "carousel":
            return "carousel"
        if r == "photo":
            return "image"
        return r or "other"
    # facebook
    if r == "photo":
        return "image"
    if r == "video":
        return "video"
    if r == "status":
        return "text"
    if r == "link":
        return "link"
    return r or "other"


def pick_fb_rate(row: pd.Series) -> float | None:
    for col in ("engagement_rate_by_page_fan", "engagement_rate_by_estimated_impression"):
        if col in row.index and pd.notna(row[col]):
            try:
                return float(row[col])
            except (TypeError, ValueError):
                pass
    return None


def pick_ig_rate(row: pd.Series) -> float | None:
    for col in ("engagement_rate_by_follower", "engagement_rate_by_estimated_impression"):
        if col in row.index and pd.notna(row[col]):
            try:
                return float(row[col])
            except (TypeError, ValueError):
                pass
    return None


def load_facebook(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    rows = []
    for _, row in df.iterrows():
        msg = row.get("message")
        if pd.isna(msg):
            msg = ""
        else:
            msg = str(msg)
        themes, primary = score_themes(msg)
        link_title = row.get("link_title")
        link_desc = row.get("link_description")
        extra = " ".join(
            str(x) for x in (link_title, link_desc) if pd.notna(x) and str(x).strip()
        )
        if extra:
            th2, pr2 = score_themes(extra)
            themes = list(dict.fromkeys(themes + th2))
            if primary == "sin_clasificar" and pr2 != "sin_clasificar":
                primary = pr2
        pub = row.get("published_at")
        pub_iso = None
        if pd.notna(pub):
            try:
                pub_iso = pd.to_datetime(pub, errors="coerce")
                if pd.notna(pub_iso):
                    pub_iso = pub_iso.isoformat()
            except Exception:
                pub_iso = str(pub)

        rows.append(
            {
                "platform": "facebook",
                "company": str(row.get("company", "")).strip(),
                "presence_handle": str(row.get("presence_handle", "") or "").strip(),
                "published_at": pub_iso,
                "message": msg[:1200],
                "post_link": str(row.get("post_link", "") or ""),
                "post_type_raw": str(row.get("post_type", "") or ""),
                "format_normalized": normalize_format("facebook", str(row.get("post_type", "") or "")),
                "themes": themes,
                "theme_primary": primary,
                "hashtags": [],
                "engagement_total": _safe_float(row.get("engagement_total")),
                "engagement_rate_audience": pick_fb_rate(row),
                "comments": _safe_int(row.get("comments")),
                "shares": _safe_int(row.get("shares")),
                "page_fans": _safe_int(row.get("page_fans")),
                "post_tag_ugc": _safe_int(row.get("post_tag_ugc")),
                "post_tag_contests": _safe_int(row.get("post_tag_contests")),
            }
        )
    return pd.DataFrame(rows)


def _safe_float(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def load_instagram(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    rows = []
    for _, row in df.iterrows():
        msg = row.get("message")
        if pd.isna(msg):
            msg = ""
        else:
            msg = str(msg)
        themes, primary = score_themes(msg)
        tags = extract_hashtags(msg)
        pub = row.get("published_at")
        pub_iso = None
        if pd.notna(pub):
            try:
                pub_iso = pd.to_datetime(pub, errors="coerce")
                if pd.notna(pub_iso):
                    pub_iso = pub_iso.isoformat()
            except Exception:
                pub_iso = str(pub)

        rows.append(
            {
                "platform": "instagram",
                "company": str(row.get("company", "")).strip(),
                "presence_handle": str(row.get("presence_handle", "") or "").strip(),
                "published_at": pub_iso,
                "message": msg[:1200],
                "post_link": str(row.get("post_link", "") or ""),
                "post_type_raw": str(row.get("post_type", "") or ""),
                "format_normalized": normalize_format("instagram", str(row.get("post_type", "") or "")),
                "themes": themes,
                "theme_primary": primary,
                "hashtags": tags[:40],
                "engagement_total": _safe_float(row.get("engagement_total")),
                "engagement_rate_audience": pick_ig_rate(row),
                "likes": _safe_float(row.get("likes")),
                "comments": _safe_int(row.get("comments")),
                "followers": _safe_int(row.get("followers")),
                "post_tag_ugc": _safe_int(row.get("post_tag_ugc")),
                "post_tag_contests": _safe_int(row.get("post_tag_contests")),
            }
        )
    return pd.DataFrame(rows)


def find_csv_files() -> tuple[Path | None, Path | None]:
    if not DATA_DIR.is_dir():
        return None, None
    fb = None
    ig = None
    for p in DATA_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != ".csv":
            continue
        name = p.name.lower()
        if "facebook" in name and "rivaliq" in name:
            fb = p
        if "instagram" in name and "rivaliq" in name:
            ig = p
    return fb, ig


def aggregate_posts(df: pd.DataFrame) -> dict:
    by_brand: dict = defaultdict(lambda: {"n_posts": 0, "facebook": 0, "instagram": 0})
    by_theme: dict = defaultdict(lambda: {"count": 0, "engagement_rates": [], "by_platform": defaultdict(int)})
    by_format: dict = defaultdict(lambda: {"count": 0, "engagement_rates": []})
    theme_format: dict = defaultdict(lambda: defaultdict(int))

    for _, row in df.iterrows():
        brand = row["company"] or "—"
        plat = row["platform"]
        by_brand[brand]["n_posts"] += 1
        by_brand[brand][plat] += 1
        tp = row["theme_primary"]
        by_theme[tp]["count"] += 1
        by_theme[tp]["by_platform"][plat] += 1
        er = row.get("engagement_rate_audience")
        if er is not None and isinstance(er, (int, float)):
            by_theme[tp]["engagement_rates"].append(float(er))
        fmt = row["format_normalized"]
        by_format[fmt]["count"] += 1
        if er is not None and isinstance(er, (int, float)):
            by_format[fmt]["engagement_rates"].append(float(er))
        theme_format[tp][fmt] += 1

    def avg(lst: list) -> float | None:
        if not lst:
            return None
        return sum(lst) / len(lst)

    by_theme_out = {}
    for k, v in by_theme.items():
        by_theme_out[k] = {
            "count": v["count"],
            "avg_engagement_rate_audience": avg(v["engagement_rates"]),
            "by_platform": dict(v["by_platform"]),
        }

    by_format_out = {}
    for k, v in by_format.items():
        by_format_out[k] = {
            "count": v["count"],
            "avg_engagement_rate_audience": avg(v["engagement_rates"]),
        }

    theme_format_out = {t: dict(fm) for t, fm in theme_format.items()}

    return {
        "by_brand": {k: dict(v) for k, v in sorted(by_brand.items(), key=lambda x: -x[1]["n_posts"])},
        "by_theme_primary": dict(sorted(by_theme_out.items(), key=lambda x: -x[1]["count"])),
        "by_format": dict(sorted(by_format_out.items(), key=lambda x: -x[1]["count"])),
        "cross_theme_format": theme_format_out,
    }


def hashtag_counts(df: pd.DataFrame, top_n: int = 40) -> list[dict]:
    if "hashtags" not in df.columns:
        return []
    freq: dict[str, int] = defaultdict(int)
    for tags in df["hashtags"]:
        if not isinstance(tags, list):
            continue
        for t in tags:
            freq[t.lower()] += 1
    sorted_items = sorted(freq.items(), key=lambda x: -x[1])[:top_n]
    return [{"tag": a, "count": b} for a, b in sorted_items]


def build_payload(fb_path: Path, ig_path: Path) -> dict:
    dfb = load_facebook(fb_path)
    dfi = load_instagram(ig_path)
    posts_df = pd.concat([dfb, dfi], ignore_index=True)

    brands_fb = set(dfb["company"].unique())
    brands_ig = set(dfi["company"].unique())
    all_brands = sorted(brands_fb | brands_ig)

    aggregates = aggregate_posts(posts_df)
    ig_only = posts_df[posts_df["platform"] == "instagram"]
    hashtag_top = hashtag_counts(ig_only)

    posts_records = posts_df.to_dict(orient="records")

    return {
        "meta": {
            "classification_version": "1.0-rules",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": {
                "facebook": str(fb_path.relative_to(ROOT)).replace("\\", "/"),
                "instagram": str(ig_path.relative_to(ROOT)).replace("\\", "/"),
            },
            "row_counts": {"facebook": len(dfb), "instagram": len(dfi), "total": len(posts_df)},
            "brands_distinct": all_brands,
            "notes": [
                "Export tipo 'top landscape posts' de Rival IQ: la muestra prioriza posts relevantes del paisaje; no es necesariamente el universo completo de publicaciones.",
                "Tema y formato se derivan por reglas de palabras clave (ES/EN); revisar manualmente una muestra para afinar.",
                "engagement_rate_audience: Facebook usa tasa vs fans de página; Instagram vs seguidores, cuando el campo existe en el CSV.",
                "Marcas sin filas en un canal: sin datos en el export o sin actividad en el periodo analizado.",
            ],
        },
        "aggregates": aggregates,
        "hashtags_top_instagram": hashtag_top,
        "posts": posts_records,
    }


def main() -> Path | None:
    fb, ig = find_csv_files()
    if not fb or not ig:
        print("Social benchmark: CSV no encontrados en data/ (rivaliq * facebook.csv / instagram.csv). Omitido.")
        return None
    payload = build_payload(fb, ig)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote:", OUT_JSON)
    return OUT_JSON


if __name__ == "__main__":
    main()
