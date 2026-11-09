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


def _usd(valor):
    """Para los mensajes: 309 en vez de 309.0, y 309.50 cuando hay centavos."""
    redondeado = Decimal(valor).quantize(Decimal("0.01"))
    return f"{redondeado:.0f}" if redondeado == redondeado.to_integral_value() else f"{redondeado:.2f}"


def _lista_corta(items, maximo=5):
    """'a, b, c y 4 más' para que el mensaje se lea de un vistazo."""
    if len(items) <= maximo:
        return ", ".join(items)
    return f"{', '.join(items[:maximo])} y {len(items) - maximo} más"


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
                f"{plataforma} muestra {etiqueta} a USD {_usd(precio)} la noche {noche}, "
                f"{abs(_pct(diferencia))}% menos que la web del hotel (USD {_usd(propio)})."
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
                    f"de USD {_usd(anterior)} a USD {_usd(precio)} ({_pct(variacion):+}%)."
                ),
            })

    return eventos, {"precio": precio, "disponible": True, "menor": menor}


def hash_amenities(conjunto):
    return hashlib.sha256("|".join(conjunto).encode("utf-8")).hexdigest()[:16]


def evaluar_amenities(plataforma, amenities_plataforma, amenities_hotel, previo):
    """
    previo: {'conjunto', 'hash', 'faltantes'} o None. Devuelve (evento|None, estado_nuevo).

    Se avisa por lo que el hotel ofrece y la plataforma no muestra (faltantes). No se compara
    contra la lectura anterior de la plataforma: el modelo lee a veces la lista corta y a veces
    la completa de instalaciones, y eso no es un cambio del hotel. Solo se avisa cuando el
    conjunto de faltantes cambia, para no repetir la alerta en cada ejecución.
    """
    conjunto = normalizar_amenities(amenities_plataforma)
    if not conjunto:
        # Lista vacía = lectura fallida, no "el hotel no tiene nada".
        return None, previo

    faltantes = faltantes_en(amenities_hotel, amenities_plataforma)
    estado = {"conjunto": conjunto, "hash": hash_amenities(conjunto), "faltantes": faltantes}

    anteriores = list((previo or {}).get("faltantes", []))
    if not faltantes or faltantes == anteriores:
        return None, estado

    return {
        "tipo": "AMENITIES_DISTINTAS",
        "plataforma": plataforma,
        "faltantes": faltantes,
        "mensaje": f"{plataforma} no muestra {len(faltantes)} amenities del hotel: {_lista_corta(faltantes)}.",
    }, estado
