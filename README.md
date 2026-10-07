# Scraper por id

Recorre `URL_TEMPLATE` cambiando `{id}` desde `START_ID` hasta `END_ID`, extrae los campos de `SELECTORS` y los guarda en PostgreSQL (o SQLite en local). Si se para, continúa por el último id guardado.

## Configuración

| Variable | Ejemplo | Para qué |
|---|---|---|
| `URL_TEMPLATE` | `https://web.com/ficha/{id}` | URL con `{id}` donde va el número |
| `SELECTORS` | `{"titulo": "h1", "imagen": "img.foto@src"}` | Campo → selector CSS. `@atributo` lee un atributo |
| `START_ID` / `END_ID` | `1` / `5000` | Rango de ids |
| `DELAY_SECONDS` | `2` | Pausa entre peticiones (no lo bajes mucho) |
| `MAX_CONSECUTIVE_MISSES` | `50` | Para tras N ids seguidos sin página (0 = nunca) |
| `AUTO_START` | `true` | Arranca solo al iniciar el servicio |
| `ADMIN_TOKEN` | (generado) | Protege `/start` y `/stop` |

## Endpoints

- `GET /` — estado y progreso
- `POST /start` · `POST /stop` — cabecera `X-Admin-Token: <token>`
- `GET /items?limit=50&offset=0` · `GET /items/{id}`
- `GET /export.csv` — descarga todo en CSV
- `GET /docs` — interfaz para probar la API

## Desplegar en Render

1. Sube este proyecto a un repositorio de GitHub.
2. En Render: **New → Blueprint** y elige el repositorio. Crea el servicio web y la base de datos a partir de `render.yaml`.
3. Rellena `URL_TEMPLATE` y ajusta `SELECTORS`.
4. Arranca: `curl -X POST https://TU-SERVICIO.onrender.com/start -H "X-Admin-Token: TU_TOKEN"` (el token está en Environment).

## En local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
URL_TEMPLATE="https://web.com/ficha/{id}" uvicorn app.main:app --reload
```
