"""
Lee dashboard/social_data.json y exporta tablas CSV + INSIGHTS.md para el benchmark de redes.

Ejecutar tras: python scripts/build_social_benchmark.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "dashboard" / "social_data.json"
OUT_DIR = ROOT / "reports" / "social_benchmark"


def load_posts() -> pd.DataFrame:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    posts = data.get("posts") or []
    return pd.DataFrame(posts)


def pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 2) if total else 0.0


def safe_median(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float(s.median())


def safe_mean(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float(s.mean())


def expand_classification(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    def macro_id(c):
        if not isinstance(c, dict):
            return "sin_clasificar"
        return (c.get("macro") or {}).get("id") or "sin_clasificar"

    def macro_label(c):
        if not isinstance(c, dict):
            return ""
        return (c.get("macro") or {}).get("label") or ""

    def category_id(c):
        if not isinstance(c, dict):
            return "sin_clasificar"
        return (c.get("category") or {}).get("id") or "sin_clasificar"

    def category_label(c):
        if not isinstance(c, dict):
            return ""
        return (c.get("category") or {}).get("label") or ""

    def subcategory_id(c):
        if not isinstance(c, dict):
            return "sin_clasificar"
        return (c.get("subcategory") or {}).get("id") or "sin_clasificar"

    def intent_val(c):
        if not isinstance(c, dict):
            return "consideration"
        return c.get("intent") or "consideration"

    out["macro_id"] = out["classification"].map(macro_id)
    out["macro_label"] = out["classification"].map(macro_label)
    out["category_id"] = out["classification"].map(category_id)
    out["category_label"] = out["classification"].map(category_label)
    out["subcategory_id"] = out["classification"].map(subcategory_id)
    out["intent"] = out["classification"].map(intent_val)
    return out


def mix_table(df: pd.DataFrame, col: str, total: int) -> pd.DataFrame:
    vc = df[col].value_counts()
    rows = []
    for val, c in vc.items():
        sub = df[df[col] == val]
        rows.append(
            {
                col: val,
                "count": int(c),
                "pct_of_total": pct(int(c), total),
                "median_er_audience": safe_median(sub["engagement_rate_audience"]),
                "median_estimated_impressions": safe_median(sub["estimated_impressions"]),
            }
        )
    return pd.DataFrame(rows)


def perf_table(df: pd.DataFrame, col: str, min_n: int = 1) -> pd.DataFrame:
    rows = []
    for val, sub in df.groupby(col, dropna=False):
        n = len(sub)
        if n < min_n:
            continue
        rows.append(
            {
                col: val,
                "n": n,
                "median_er_audience": safe_median(sub["engagement_rate_audience"]),
                "mean_er_audience": safe_mean(sub["engagement_rate_audience"]),
                "median_impressions": safe_median(sub["estimated_impressions"]),
                "mean_impressions": safe_mean(sub["estimated_impressions"]),
                "n_with_impressions": int(sub["estimated_impressions"].notna().sum()),
                "median_er_per_impression": safe_median(sub["engagement_rate_by_estimated_impression"]),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("n", ascending=False)
    return out


def brand_macro_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Filas = marca, columnas = % de posts de esa marca en cada macro."""
    total_by_brand = df.groupby("company_canonical").size()
    pivot = df.pivot_table(
        index="company_canonical",
        columns="macro_id",
        aggfunc="size",
        fill_value=0,
    )
    pct_rows = pivot.div(total_by_brand, axis=0) * 100.0
    pct_rows = pct_rows.round(2)
    pct_rows["post_count"] = total_by_brand
    return pct_rows.reset_index().sort_values("post_count", ascending=False)


def assign_quadrant(df: pd.DataFrame) -> pd.DataFrame:
    """Alto/bajo respecto a la mediana global de ER y de impresiones (solo filas con ambas métricas)."""
    sub = df[df["estimated_impressions"].notna() & df["engagement_rate_audience"].notna()].copy()
    if sub.empty:
        return sub
    mer = sub["engagement_rate_audience"].median()
    mim = sub["estimated_impressions"].median()
    sub["quadrant"] = sub.apply(
        lambda r: (
            ("alto_ER" if r["engagement_rate_audience"] >= mer else "bajo_ER")
            + "_"
            + ("alto_alcance" if r["estimated_impressions"] >= mim else "bajo_alcance")
        ),
        axis=1,
    )
    sub["median_ref_er"] = mer
    sub["median_ref_impressions"] = mim
    return sub


def export_top_posts(df: pd.DataFrame, n: int = 25) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = [
        "company_canonical",
        "platform",
        "macro_id",
        "category_id",
        "intent",
        "format_normalized",
        "engagement_rate_audience",
        "estimated_impressions",
        "engagement_rate_by_estimated_impression",
        "post_link",
        "message",
    ]
    base = df[[c for c in cols if c in df.columns]]
    by_er = base.dropna(subset=["engagement_rate_audience"]).nlargest(n, "engagement_rate_audience")
    by_imp = base.dropna(subset=["estimated_impressions"]).nlargest(n, "estimated_impressions")
    return by_er, by_imp


def stratified_sample(df: pd.DataFrame, k: int = 30) -> pd.DataFrame:
    """Muestra para revisión manual: hasta k posts con reparto por macro."""
    if df.empty:
        return df
    df = df.reset_index(drop=True)
    macros = df["macro_id"].unique().tolist()
    per = max(1, k // max(len(macros), 1))
    chunks: list[pd.DataFrame] = []
    taken = set()
    for macro in macros:
        sub = df[df["macro_id"] == macro]
        n = min(per, len(sub))
        part = sub.sample(n=n, random_state=42)
        chunks.append(part)
        taken.update(part.index.tolist())
    out = pd.concat(chunks, ignore_index=True) if chunks else df.iloc[0:0]
    if len(out) < k:
        rest_idx = [i for i in df.index if i not in taken]
        need = min(k - len(out), len(rest_idx))
        if need > 0:
            extra = df.loc[rest_idx].sample(n=need, random_state=43)
            out = pd.concat([out, extra.reset_index(drop=True)], ignore_index=True)
    return out.head(k)


def df_to_md(table: pd.DataFrame) -> str:
    try:
        return table.to_markdown(index=False)
    except (ImportError, ValueError, OSError):
        return table.to_csv(index=False)


def write_insights_md(
    df: pd.DataFrame,
    meta: dict,
    perf_macro: pd.DataFrame,
    mix_macro: pd.DataFrame,
    brand_sum: pd.DataFrame,
    path: Path,
) -> None:
    total = len(df)
    n_imp = int(df["estimated_impressions"].notna().sum())
    lines = [
        "# Benchmark de contenido — redes sociales (generado)",
        "",
        "## Contexto y datos",
        "",
        f"- **Publicaciones analizadas:** {total}",
        f"- **Con impresiones estimadas:** {n_imp} ({pct(n_imp, total)}% del total)",
        f"- **Versión de clasificación:** {meta.get('classification_version', 'n/d')}",
        f"- **Fuente:** export Rival IQ tipo *top landscape posts* (muestra priorizada de alto desempeño en el panel; no es un censo de todo lo publicado).",
        "",
        "## Limitaciones",
        "",
        "- Las impresiones y la tasa por impresión son **estimaciones** del proveedor, no cifras auditadas.",
        "- Comparar Facebook vs Instagram en la misma métrica de `engagement_rate_audience` tiene sesgo de definición (fans vs seguidores); interpretar tendencias dentro de cada red y usar `engagement_rate_by_estimated_impression` como cruce con alcance.",
        "- Celdas con **pocos posts** (subcategorías raras) pueden tener medianas inestables.",
        "",
        "## Mix de contenido (macro)",
        "",
        df_to_md(mix_macro),
        "",
        "## Rendimiento por macro (mediana de ER vs audiencia y de impresiones)",
        "",
        df_to_md(perf_macro.head(12)),
        "",
        "## Competidores (resumen)",
        "",
        df_to_md(brand_sum.head(15)),
        "",
        "## Lecturas sugeridas para Vitralogic",
        "",
    ]

    # Auto bullets from data
    if not perf_macro.empty:
        er_ok = perf_macro.dropna(subset=["median_er_audience"])
        if not er_ok.empty:
            top_er = er_ok.sort_values("median_er_audience", ascending=False).iloc[0]
            lines.append(
                f"- **Mayor mediana de engagement (vs audiencia) entre macros:** {top_er['macro_id']} "
                f"(mediana ER ≈ {float(top_er['median_er_audience']):.4f})."
            )
        imp_ok = perf_macro.dropna(subset=["median_impressions"])
        if not imp_ok.empty:
            top_imp = imp_ok.sort_values("median_impressions", ascending=False).iloc[0]
            lines.append(
                f"- **Mayor mediana de impresiones estimadas entre macros:** {top_imp['macro_id']} "
                f"(mediana impresiones ≈ {float(top_imp['median_impressions']):.0f})."
            )
    mix_rows = mix_macro.set_index("macro_id")["pct_of_total"].sort_values(ascending=False)
    if not mix_rows.empty:
        dom = mix_rows.index[0]
        lines.append(
            f"- **Macro más frecuente en la muestra:** {dom} ({mix_rows.iloc[0]:.1f}% de posts)."
        )
    sin_n = int((df["macro_id"] == "sin_clasificar").sum())
    lines.append(
        f"- **Posts sin clasificar taxonómica:** {sin_n} ({pct(sin_n, total)}%) — conviene revisar keywords o etiquetado manual en una segunda iteración."
    )
    lines.extend(
        [
            "",
            "## Archivos CSV adjuntos",
            "",
            "Ver la misma carpeta: `01_mix_macro.csv` … `04_mix_format_platform.csv`, `10_perf_macro.csv` … `14_perf_format_min10.csv`, `20_brand_summary.csv`, `21_brand_macro_pct.csv`, `30_quadrant_posts.csv`, `31_quadrant_counts.csv`, `40_top_posts_by_er_audience.csv`, `41_top_posts_by_impressions.csv`, `42_sample_review_posts.csv`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    if not JSON_PATH.is_file():
        raise SystemExit(f"No se encontró {JSON_PATH}. Ejecuta primero build_social_benchmark.py.")

    raw = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    meta = raw.get("meta") or {}
    df = load_posts()
    if df.empty:
        raise SystemExit("No hay posts en el JSON.")

    df = expand_classification(df)
    total = len(df)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Mix
    mix_macro = mix_table(df, "macro_id", total)
    mix_macro.to_csv(OUT_DIR / "01_mix_macro.csv", index=False)
    mix_table(df, "category_id", total).to_csv(OUT_DIR / "02_mix_category.csv", index=False)
    mix_table(df, "intent", total).to_csv(OUT_DIR / "03_mix_intent.csv", index=False)

    fmt_plat = df.groupby(["format_normalized", "platform"]).size().reset_index(name="count")
    fmt_plat["pct"] = (fmt_plat["count"] / total * 100).round(2)
    fmt_plat.to_csv(OUT_DIR / "04_mix_format_platform.csv", index=False)

    # Performance
    perf_macro = perf_table(df, "macro_id", min_n=1)
    perf_macro.to_csv(OUT_DIR / "10_perf_macro.csv", index=False)
    perf_table(df, "category_id", min_n=5).to_csv(OUT_DIR / "11_perf_category_min5.csv", index=False)
    perf_table(df, "subcategory_id", min_n=8).to_csv(OUT_DIR / "12_perf_subcategory_min8.csv", index=False)
    perf_table(df, "intent", min_n=1).to_csv(OUT_DIR / "13_perf_intent.csv", index=False)

    pf = perf_table(df, "format_normalized", min_n=10)
    pf.to_csv(OUT_DIR / "14_perf_format_min10.csv", index=False)

    # Brand
    perf_table(df, "company_canonical", min_n=1).to_csv(OUT_DIR / "20_brand_summary.csv", index=False)
    brand_macro_shares(df).to_csv(OUT_DIR / "21_brand_macro_pct.csv", index=False)

    # Quadrants
    qdf = assign_quadrant(df)
    if not qdf.empty:
        qdf["message"] = qdf["message"].str.slice(0, 200)
        qdf.to_csv(OUT_DIR / "30_quadrant_posts.csv", index=False)
        qdf.groupby("quadrant").size().reset_index(name="count").to_csv(
            OUT_DIR / "31_quadrant_counts.csv", index=False
        )

    top_er, top_imp = export_top_posts(df, 25)
    top_er.to_csv(OUT_DIR / "40_top_posts_by_er_audience.csv", index=False)
    top_imp.to_csv(OUT_DIR / "41_top_posts_by_impressions.csv", index=False)

    stratified_sample(df, 30).assign(
        message=lambda x: x["message"].str.slice(0, 220)
    ).to_csv(OUT_DIR / "42_sample_review_posts.csv", index=False)

    brand_sum = perf_table(df, "company_canonical", min_n=1)
    write_insights_md(df, meta, perf_macro, mix_macro, brand_sum, OUT_DIR / "INSIGHTS.md")

    print("Wrote reports to:", OUT_DIR)


if __name__ == "__main__":
    main()
