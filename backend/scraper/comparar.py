"""
Decisiones de alerta del scraper (sección 6.7). Funciones puras: reciben lo observado
y el estado anterior, devuelven los eventos a publicar y el estado nuevo.

- Comparación 1: contra la observación anterior (CAMBIO_PRECIO si la variación llega al umbral).
- Comparación 2: contra la tarifa propia (PRECIO_MENOR_EN_PLATAFORMA si la plataforma es más
  barata que la web del hotel por más del umbral).
- Sin alertas repetidas: se avisa cuando CAMBIA el estado, no en cada ejecución.
- Sin tarifas disponibles NO es precio cero: queda disponible=False y no hay alerta.
"""
import hashlib
from decimal import Decimal

from normalizar import faltantes_en, normalizar_amenities

MONEDA = "USD"


def _pct(valor):
    return round(float(valor) * 100, 1)


def _dinero(valor):
    return float(Decimal(valor).quantize(Decimal("0.01")))


def evaluar_precio(
    plataforma, noche, tipo_id, precio, propio, previo,
    umbral_menor=Decimal("0.02"), umbral_cambio=Decimal("0.10"),
):
    """
    previo: dict con 'precio' (Decimal o None) y 'menor' (bool) de la observación anterior,
    o None si es la primera vez. Devuelve (eventos, estado_nuevo).
    """
    if precio is None:
        # Sin tarifas: no hay precio, no hay alerta, y deja de contar como "más barato".
        return [], {"precio": None, "disponible": False, "menor": False}

    eventos = []
    base = {
        "plataforma": plataforma,
        "tipoHabitacion": tipo_id,
        "noche": noche,
        "moneda": MONEDA,
    }
    etiqueta = tipo_id or "la habitación base"

    diferencia = (precio - propio) / propio
    menor = diferencia < -umbral_menor
    previo_menor = bool(previo and previo.get("menor"))

    if menor and not previo_menor:
        eventos.append({
            "tipo": "PRECIO_MENOR_EN_PLATAFORMA",
            **base,
            "precioPlataforma": _dinero(precio),
            "precioPropio": _dinero(propio),
            "diferenciaPct": _pct(diferencia),
            "mensaje": (
                f"{plataforma} muestra {etiqueta} a USD {_dinero(precio)} la noche {noche}, "
                f"{abs(_pct(diferencia))}% menos que la web del hotel (USD {_dinero(propio)})."
            ),
        })

    anterior = previo.get("precio") if previo else None
    if anterior:
        variacion = (precio - anterior) / anterior
        if abs(variacion) >= umbral_cambio:
            eventos.append({
                "tipo": "CAMBIO_PRECIO",
                **base,
                "precioAnterior": _dinero(anterior),
                "precioNuevo": _dinero(precio),
                "variacionPct": _pct(variacion),
                "mensaje": (
                    f"{plataforma} cambió {etiqueta} la noche {noche}: "
                    f"de USD {_dinero(anterior)} a USD {_dinero(precio)} ({_pct(variacion):+}%)."
                ),
            })

    return eventos, {"precio": precio, "disponible": True, "menor": menor}


def hash_amenities(conjunto):
    return hashlib.sha256("|".join(conjunto).encode("utf-8")).hexdigest()[:16]


def evaluar_amenities(plataforma, amenities_plataforma, amenities_hotel, previo):
    """
    previo: {'conjunto': [...], 'hash': '...'} o None. Devuelve (evento|None, estado_nuevo).
    - Si cambió respecto de la observación anterior: se informa lo agregado y lo quitado.
    - En la primera observación: se informa lo que el hotel ofrece y la plataforma no muestra.
    Solo se avisa cuando la diferencia es nueva (mismo conjunto = sin alerta repetida).
    """
    conjunto = normalizar_amenities(amenities_plataforma)
    if not conjunto:
        # Lista vacía = lectura fallida, no "el hotel no tiene nada".
        return None, previo

    estado = {"conjunto": conjunto, "hash": hash_amenities(conjunto)}
    if previo and previo.get("hash") == estado["hash"]:
        return None, estado

    if previo:
        agregadas = sorted(set(conjunto) - set(previo.get("conjunto", [])))
        quitadas = sorted(set(previo.get("conjunto", [])) - set(conjunto))
        faltantes = []
    else:
        agregadas, quitadas = [], []
        faltantes = faltantes_en(amenities_hotel, amenities_plataforma)

    if not (agregadas or quitadas or faltantes):
        return None, estado

    partes = []
    if faltantes:
        partes.append(f"no muestra: {', '.join(faltantes)}")
    if agregadas:
        partes.append(f"agregó: {', '.join(agregadas)}")
    if quitadas:
        partes.append(f"quitó: {', '.join(quitadas)}")

    return {
        "tipo": "AMENITIES_DISTINTAS",
        "plataforma": plataforma,
        "faltantes": faltantes,
        "agregadas": agregadas,
        "quitadas": quitadas,
        "mensaje": f"Las amenities en {plataforma} difieren: " + "; ".join(partes) + ".",
    }, estado
