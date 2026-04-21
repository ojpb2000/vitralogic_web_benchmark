# Benchmark de contenido — redes sociales (generado)

## Contexto y datos

- **Publicaciones analizadas:** 1984
- **Con impresiones estimadas:** 1984 (100.0% del total)
- **Versión de clasificación:** 3.0-gemini-hybrid
- **Fuente:** export Rival IQ tipo *top landscape posts* (muestra priorizada de alto desempeño en el panel; no es un censo de todo lo publicado).

## Limitaciones

- Las impresiones y la tasa por impresión son **estimaciones** del proveedor, no cifras auditadas.
- Comparar Facebook vs Instagram en la misma métrica de `engagement_rate_audience` tiene sesgo de definición (fans vs seguidores); interpretar tendencias dentro de cada red y usar `engagement_rate_by_estimated_impression` como cruce con alcance.
- Celdas con **pocos posts** (subcategorías raras) pueden tener medianas inestables.

## Mix de contenido (macro)

macro_id,count,pct_of_total,median_er_audience,median_estimated_impressions
marca,473,23.84,0.0009528346831824,143.0
comercial,442,22.28,0.0141843971631205,110.0
inspiracional,402,20.26,0.0045019696117051,145.5
educativo,389,19.61,0.0028137310073157,96.0
sin_clasificar,226,11.39,0.0006352231221216,127.0
prueba_confianza,52,2.62,0.0050647158131682,145.0


## Rendimiento por macro (mediana de ER vs audiencia y de impresiones)

macro_id,n,median_er_audience,mean_er_audience,median_impressions,mean_impressions,n_with_impressions,median_er_per_impression
marca,473,0.0009528346831824,0.004021285711256214,143.0,402.82241014799155,473,0.015267175572519
comercial,442,0.0141843971631205,0.025125209035831778,110.0,147.65837104072398,442,0.0215440550785178
inspiracional,402,0.0045019696117051,0.011356315515787323,145.5,263.1318407960199,402,0.017924716902997302
educativo,389,0.0028137310073157,0.007897557048229573,96.0,197.0694087403599,389,0.0212765957446808
sin_clasificar,226,0.0006352231221216,0.002465733622578704,127.0,322.12831858407077,226,0.0117647058823529
prueba_confianza,52,0.0050647158131682,0.022154849308718732,145.0,423.28846153846155,52,0.0208333333333333


## Competidores (resumen)

company_canonical,n,median_er_audience,mean_er_audience,median_impressions,mean_impressions,n_with_impressions,median_er_per_impression
Aviglass,482,0.000794028902652,0.002748961745652264,172.5,547.5767634854772,482,0.0116279069767441
VEKA México,441,0.0039392234102419,0.004242310448410137,113.0,151.3718820861678,441,0.0204081632653061
Ventanas Termo-acústicas de PVC,384,0.0256410256410256,0.0367097995090016,96.0,113.70052083333333,384,0.0263157894736842
Vetro Galo,241,0.0152905198776758,0.01690976299450161,104.0,187.36929460580913,241,0.0173745173745173
Canceles Finos,237,0.000141249588022,0.0014779409950823003,107.0,293.76371308016877,237,0.0125
Abatik,142,0.0005205622071837,0.0007808433107755874,108.5,213.92957746478874,142,0.0212765957446808
Corporación Cristel,57,0.0084269662921348,0.009215454366252663,145.0,237.66666666666666,57,0.0483870967741935


## Lecturas sugeridas para Vitralogic

- **Mayor mediana de engagement (vs audiencia) entre macros:** comercial (mediana ER ≈ 0.0142).
- **Mayor mediana de impresiones estimadas entre macros:** inspiracional (mediana impresiones ≈ 146).
- **Macro más frecuente en la muestra:** marca (23.8% de posts).
- **Posts sin clasificar taxonómica:** 226 (11.39%) — conviene revisar keywords o etiquetado manual en una segunda iteración.

## Archivos CSV adjuntos

Ver la misma carpeta: `01_mix_macro.csv` … `04_mix_format_platform.csv`, `10_perf_macro.csv` … `14_perf_format_min10.csv`, `20_brand_summary.csv`, `21_brand_macro_pct.csv`, `30_quadrant_posts.csv`, `31_quadrant_counts.csv`, `40_top_posts_by_er_audience.csv`, `41_top_posts_by_impressions.csv`, `42_sample_review_posts.csv`.
