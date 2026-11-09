"""
Lambda Scraper (secciones 4.3, 6.7 y 6.8).

EventBridge la dispara por cron. Para cada noche de la ventana consulta la ficha del propio
hotel en Booking y en Google Travel, normaliza los precios a USD con impuestos, los compara
con la tarifa propia y con la observación anterior, y publica en SQS:
PRECIO_MENOR_EN_PLATAFORMA, CAMBIO_PRECIO o AMENITIES_DISTINTAS.

Modos (evento {"modo": "demo" | "real"}, o variable SCRAPER_MODO; por defecto "demo"):
  demo  lee respuestas guardadas (carpeta demo/) en vez de abrir Booking y Google. Es el plan B
        de la demostración: esos sitios suelen bloquear el acceso automatizado desde AWS.
  real  abre los sitios con Playwright y lee las capturas con Gemini.

Evento opcional: {"noches": ["2026-11-20", ...]} para fijar las noches a consultar y
{"fuentes": ["booking"] | ["google"]} para probar una sola fuente (por defecto, las dos).
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import almacen
import comparar
import normalizar

HOTEL_TIMEZONE = os.getenv("HOTEL_TIMEZONE", "America/Montevideo")
DEMO_DIR = Path(os.getenv("SCRAPER_DEMO_DIR", Path(__file__).parent / "demo"))

# Chromium en Lambda: sin sandbox de usuario, sin /dev/shm grande, sin GPU y en un solo proceso.
ARGS_CHROMIUM_LAMBDA = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-gpu", "--single-process", "--no-zygote",
]

PLATAFORMA_BOOKING = "Booking.com"      # coincide con las claves de `nombres_ota`
PLATAFORMA_GOOGLE = "Google Travel"


def _config():
    """Se lee en cada ejecución para que las pruebas y los cambios de entorno surtan efecto."""
    return {
        "modo": os.getenv("SCRAPER_MODO", "demo"),
        "offsets": [int(d) for d in os.getenv("SCRAPER_OFFSETS_DIAS", "7").split(",") if d.strip()],
        "umbral_menor": Decimal(os.getenv("SCRAPER_UMBRAL_MENOR", "0.02")),
        "umbral_cambio": Decimal(os.getenv("SCRAPER_UMBRAL_CAMBIO", "0.10")),
        "concurrencia": int(os.getenv("SCRAPER_CONCURRENCIA", "2")),
        "timeout_tarea": int(os.getenv("SCRAPER_TIMEOUT_TAREA", "240")),
        "tipos_de_cambio": normalizar.parsear_tipos_de_cambio(os.getenv("SCRAPER_TIPOS_DE_CAMBIO", "")),
        "hotel_nombre": os.getenv("SCRAPER_HOTEL_NOMBRE", "Aloft Montevideo"),
        "pais": os.getenv("SCRAPER_PAIS", "uy"),
        "ciudad": os.getenv("SCRAPER_CIUDAD", "Montevideo"),
        "propios": [p.strip().lower() for p in os.getenv("SCRAPER_PROVEEDORES_PROPIOS", "marriott,aloft").split(",") if p.strip()],
        "amenities": os.getenv("SCRAPER_AMENITIES", "true").lower() == "true",
        "fuentes": [f.strip() for f in os.getenv("SCRAPER_FUENTES", "booking,google").split(",") if f.strip()],
    }


# -----------------------------
# Fuentes de datos
# -----------------------------

def _cargar_demo(nombre):
    with open(DEMO_DIR / nombre, encoding="utf-8") as archivo:
        return json.load(archivo)


async def _consultar_fuentes(noches, cfg):
    """
    Devuelve {(fuente, noche): datos|{"error": ...}} para Booking y Google Travel.
    En modo real se limita la concurrencia: cada tarea abre un Chromium que consume
    cientos de MB y los sitios y la API del modelo limitan el ritmo (sección 6.8).
    """
    if cfg["modo"] != "real":
        demo = {"booking": _cargar_demo("booking.json"), "google": _cargar_demo("google_travel.json")}
        return {(f, n): demo[f] for f in cfg["fuentes"] for n in noches}

    # Importación diferida: exige GEMINI_API_KEY y Playwright, que el modo demo no necesita.
    from booking_scraper import booking_data
    from google_travel_scraper import google_travel_data
    import booking_scraper
    import google_travel_scraper

    for modulo in (booking_scraper, google_travel_scraper):
        modulo.CHROMIUM_ARGS[:] = ARGS_CHROMIUM_LAMBDA

    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        # En Lambda solo se escribe en /tmp: los scrapers crean carpetas de depuración
        # en el directorio actual y Chromium necesita un HOME escribible.
        # Va después de los imports porque estos buscan los módulos en el directorio actual.
        os.environ["HOME"] = "/tmp"
        os.chdir("/tmp")
    # Registro del navegador en CloudWatch (por qué se cerró, qué argumentos usó).
    os.environ.setdefault("DEBUG", "pw:browser")

    semaforo = asyncio.Semaphore(cfg["concurrencia"])

    async def tarea(fuente, noche):
        async with semaforo:
            try:
                if fuente == "booking":
                    coro = booking_data(cfg["hotel_nombre"], cfg["pais"], noche)
                else:
                    coro = google_travel_data(cfg["hotel_nombre"], noche, city=cfg["ciudad"])
                return (fuente, noche), await asyncio.wait_for(coro, timeout=cfg["timeout_tarea"])
            except Exception as error:  # noqa: BLE001
                return (fuente, noche), {"error": f"{type(error).__name__}: {error}"}

    resultados = await asyncio.gather(*[tarea(f, n) for f in cfg["fuentes"] for n in noches])
    return dict(resultados)


# -----------------------------
# Procesamiento de cada fuente
# -----------------------------

def _procesar_booking(datos, noche, tipos, cfg, observado_en, resumen):
    """Una tarifa por tipo propio: la más barata de las que Booking muestra para ese tipo."""
    resultados = datos.get("results") if "results" in datos else datos
    if "error" in datos or not resultados or not resultados.get("rates"):
        resumen["fuentes_sin_datos"].append(f"{PLATAFORMA_BOOKING} {noche}: {datos.get('error') or 'sin tarifas'}")
        return []

    por_tipo = {}
    for rate in resultados["rates"]:
        total, motivo = normalizar.validar_tarifa_booking(rate)
        tipo_id = normalizar.mapear_tipo(rate.get("room_name") if isinstance(rate, dict) else None, tipos, PLATAFORMA_BOOKING)
        if total is None or tipo_id is None:
            resumen["descartadas"].append(motivo or f"habitacion_sin_mapeo: {rate.get('room_name') if isinstance(rate, dict) else rate}")
            continue
        tipo = next(t for t in tipos if t["id"] == tipo_id)
        propio = almacen.tarifa_propia(tipo, noche)
        if not normalizar.precio_razonable(total, propio):
            resumen["descartadas"].append(f"precio_fuera_de_rango: {tipo_id} {total}")
            continue
        if tipo_id not in por_tipo or total < por_tipo[tipo_id][0]:
            por_tipo[tipo_id] = (total, propio)

    eventos = []
    for tipo in tipos:
        precio, propio = por_tipo.get(tipo["id"], (None, almacen.tarifa_propia(tipo, noche)))
        eventos += _evaluar_y_guardar(PLATAFORMA_BOOKING, tipo["id"], noche, precio, propio, cfg, observado_en, resumen)
    return eventos


def _procesar_google(datos, noche, tipos, cfg, observado_en, resumen):
    """
    Google Travel muestra el precio por proveedor, no por tipo de habitación: se compara con
    la tarifa propia más baja. El proveedor del propio hotel no se compara contra sí mismo.
    """
    if "error" in datos or not datos.get("prices"):
        resumen["fuentes_sin_datos"].append(f"{PLATAFORMA_GOOGLE} {noche}: {datos.get('error') or 'sin precios'}")
        return []

    tipo_base = min(tipos, key=lambda t: Decimal(str(t["precio_base"])))
    propio = almacen.tarifa_propia(tipo_base, noche)

    eventos = []
    for fila in datos["prices"]:
        proveedor = (fila or {}).get("provider") or ""
        if any(p in proveedor.lower() for p in cfg["propios"]):
            continue
        monto, moneda = normalizar.parsear_precio((fila or {}).get("price"))
        precio = normalizar.a_usd(monto, moneda, cfg["tipos_de_cambio"])
        if precio is None:
            resumen["descartadas"].append(f"precio_no_convertible: {proveedor} {(fila or {}).get('price')}")
            continue
        if not normalizar.precio_razonable(precio, propio):
            resumen["descartadas"].append(f"precio_fuera_de_rango: {proveedor} {precio}")
            continue
        eventos += _evaluar_y_guardar(
            f"{PLATAFORMA_GOOGLE} / {proveedor}", None, noche, precio, propio, cfg, observado_en, resumen
        )
    return eventos


def _evaluar_y_guardar(plataforma, tipo_id, noche, precio, propio, cfg, observado_en, resumen):
    previo = almacen.leer_precio_previo(plataforma, tipo_id, noche)
    eventos, estado = comparar.evaluar_precio(
        plataforma, noche, tipo_id, precio, propio, previo,
        umbral_menor=cfg["umbral_menor"], umbral_cambio=cfg["umbral_cambio"],
    )
    almacen.guardar_precio(plataforma, tipo_id, noche, estado, observado_en)
    resumen["observaciones"] += 1
    return eventos


def _procesar_amenities(plataforma, amenities, hotel_amenities, observado_en):
    previo = almacen.leer_amenities_previas(plataforma)
    evento, estado = comparar.evaluar_amenities(plataforma, amenities, hotel_amenities, previo)
    if estado and estado is not previo:
        almacen.guardar_amenities(plataforma, estado, observado_en)
    return [evento] if evento else []


# -----------------------------
# Handler
# -----------------------------

def _noches(evento, cfg):
    if evento.get("noches"):
        return list(evento["noches"])
    hoy = datetime.now(ZoneInfo(HOTEL_TIMEZONE)).date()
    return [(hoy + timedelta(days=d)).isoformat() for d in cfg["offsets"]]


def lambda_handler(event, context):
    event = event or {}
    cfg = _config()
    if event.get("modo"):
        cfg["modo"] = event["modo"]
    if event.get("fuentes"):
        cfg["fuentes"] = list(event["fuentes"])
    if not cfg["fuentes"] or set(cfg["fuentes"]) - {"booking", "google"}:
        raise ValueError(f"Fuentes invalidas: {cfg['fuentes']!r} (usar 'booking' y/o 'google').")
    if cfg["modo"] not in {"demo", "real"}:
        raise ValueError(f"Modo desconocido: {cfg['modo']!r} (usar 'demo' o 'real').")

    observado_en = datetime.now(timezone.utc).isoformat()
    noches = _noches(event, cfg)
    resumen = {
        "modo": cfg["modo"], "noches": noches, "observaciones": 0, "eventos": 0,
        "descartadas": [], "fuentes_sin_datos": [],
    }

    tipos = almacen.leer_tipos()
    if not tipos:
        raise RuntimeError("No hay tipos de habitación en la tabla: ejecutá el seed primero.")
    hotel_amenities = almacen.leer_amenities_del_hotel() if cfg["amenities"] else []

    fuentes = asyncio.run(_consultar_fuentes(noches, cfg))

    eventos = []
    amenities_vistas = set()
    for (fuente, noche), datos in sorted(fuentes.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        plataforma = PLATAFORMA_BOOKING if fuente == "booking" else PLATAFORMA_GOOGLE
        almacen.guardar_respaldo_crudo(plataforma, noche, observado_en, datos)
        # Lo que devolvió cada fuente (recortado): imprescindible para diagnosticar bloqueos.
        print(f"[scraper] {plataforma} {noche} respuesta: "
              f"{json.dumps(datos, ensure_ascii=False, default=str)[:1500]}")

        if fuente == "booking":
            eventos += _procesar_booking(datos, noche, tipos, cfg, observado_en, resumen)
            amenities = (datos.get("results") or datos).get("amenities")
        else:
            eventos += _procesar_google(datos, noche, tipos, cfg, observado_en, resumen)
            amenities = datos.get("amenities")

        # Las amenities no dependen de la noche: se evalúan una vez por plataforma.
        if cfg["amenities"] and plataforma not in amenities_vistas and amenities:
            amenities_vistas.add(plataforma)
            eventos += _procesar_amenities(plataforma, amenities, hotel_amenities, observado_en)

    for evento in eventos:
        if almacen.publicar_evento(evento):
            resumen["eventos"] += 1

    print(f"[scraper] resumen: {json.dumps(resumen, ensure_ascii=False, default=str)}")
    return resumen
