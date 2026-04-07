# Vitralogic — Web benchmark (dashboard)

Tablero estático de benchmark de tráfico y contenido (SimilarWeb + perfiles / auditoría). Los datos principales van **embebidos** en `dashboard/index.html` al generar el build.

**Repositorio:** [github.com/ojpb2000/vitralogic_web_benchmark](https://github.com/ojpb2000/vitralogic_web_benchmark)

## Sitio publicado (GitHub Pages)

Tras configurar Pages y hacer push a `main`, la URL será:

**https://ojpb2000.github.io/vitralogic_web_benchmark/**

### Activar GitHub Pages (una sola vez)

1. En GitHub, abre el repo → **Settings** → **Pages**.
2. En **Build and deployment**, **Source**: elige **GitHub Actions** (no “Deploy from a branch”).
3. Haz push del workflow en `.github/workflows/deploy-pages.yml` a la rama `main`. El job **Deploy GitHub Pages** subirá el contenido de la carpeta `dashboard/`.
4. En **Actions** comprueba que el workflow termine en verde; en **Pages** verás la URL cuando esté lista (puede tardar un minuto).

### Qué se publica

Solo la carpeta **`dashboard/`** (`index.html`, JSON auxiliares, `logos/`, etc.). Scripts Python y plantillas no se sirven al visitante.

## Desarrollo local

```bash
pip install -r requirements.txt
python scripts/build_dashboard_data.py
```

Luego abre `dashboard/index.html` en el navegador (o sirve la carpeta con un servidor estático).

Los Excel de SimilarWeb no están en el repo por defecto (`.gitignore`); si los tienes en `input/` u otra ruta, ajusta `scripts/build_dashboard_data.py` o quita la entrada del `.gitignore` si quieres versionarlos.

## Acceso al dashboard

El HTML incluye un login sencillo en el cliente (usuario / contraseña en el propio JS). No sustituye a un servidor con autenticación real.

## Primer push a un repo vacío

```bash
cd "Web Site Benchmark"
git init -b main
git add .
git commit -m "Initial commit: dashboard y workflow GitHub Pages"
git remote add origin https://github.com/ojpb2000/vitralogic_web_benchmark.git
git push -u origin main
```

Si el remoto ya tiene commits, usa `git pull origin main --allow-unrelated-histories` antes del push o fuerza la estrategia que prefieras.
