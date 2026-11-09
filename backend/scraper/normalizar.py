"""
Normalización de lo que lee el modelo de visión (sección 6.7). Funciones puras,
sin red ni AWS: precio como número, una sola moneda, validación y mapeo de tipos.

Criterio: todo se compara en USD con impuestos incluidos, igual que la tarifa propia.
"""
import difflib
import re
from decimal import Decimal, InvalidOperation

# Orden importa: los símbolos más específicos primero ("US$" antes que "$").
_MONEDAS = [
    ("US$", "USD"), ("USD", "USD"),
    ("UYU", "UYU"), ("$U", "UYU"), ("UY$", "UYU"),
    ("EUR", "EUR"), ("€", "EUR"),
    ("$", "USD"),  # "$" suelto: las consultas piden USD (selected_currency / curr)
]


def detectar_moneda(texto):
    texto = texto.upper().replace(" ", "")
    for simbolo, moneda in _MONEDAS:
        if simbolo in texto:
            return moneda
    return None


def _parsear_numero(chunk):
    """'8,000' -> 8000; '1.234,56' -> 1234.56; '145.50' -> 145.50; '8.000' -> 8000."""
    tiene_coma, tiene_punto = "," in chunk, "." in chunk

    if tiene_coma and tiene_punto:
        decimal = "," if chunk.rfind(",") > chunk.rfind(".") else "."
        miles = "." if decimal == "," else ","
        chunk = chunk.replace(miles, "").replace(decimal, ".")
    elif tiene_coma:
        # Una sola coma seguida de 3 dígitos (y nada más) es separador de miles.
        chunk = chunk.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", chunk) else chunk.replace(",", ".")
    elif tiene_punto:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", chunk):
            chunk = chunk.replace(".", "")
    return Decimal(chunk)


def parsear_precio(valor):
    """
    Convierte un precio leído por el modelo en (monto, moneda).
    Acepta texto ("UYU 8,000", "US$506", "$ 145.50") o números (moneda None).
    Devuelve (None, None) si no es interpretable. Nunca devuelve 0 por error.
    """
    if isinstance(valor, bool) or valor is None:
        return None, None
    if isinstance(valor, (int, float, Decimal)):
        return (Decimal(str(valor)), None) if valor > 0 else (None, None)
    if not isinstance(valor, str):
        return None, None

    encontrado = re.search(r"\d[\d.,]*", valor)
    if not encontrado:
        return None, None
    try:
        monto = _parsear_numero(encontrado.group(0).rstrip(".,"))
    except (InvalidOperation, ValueError):
        return None, None

    return (monto, detectar_moneda(valor)) if monto > 0 else (None, None)


def parsear_tipos_de_cambio(texto):
    """'UYU=40.5,EUR=0.92' -> {'UYU': Decimal('40.5'), ...} (unidades por 1 USD)."""
    tipos = {}
    for par in (texto or "").split(","):
        if "=" in par:
            moneda, valor = par.split("=", 1)
            try:
                tipos[moneda.strip().upper()] = Decimal(valor.strip())
            except InvalidOperation:
                continue
    return tipos


def a_usd(monto, moneda, tipos_de_cambio=None):
    """
    Pasa a USD. Sin moneda explícita se asume USD (se pidió USD en la URL).
    Devuelve None si la moneda no se puede convertir: no se inventa un tipo de cambio.
    """
    if monto is None:
        return None
    if moneda in (None, "USD"):
        return monto
    tasa = (tipos_de_cambio or {}).get(moneda)
    if not tasa:
        return None
    return (monto / tasa).quantize(Decimal("0.01"))


def precio_razonable(precio, referencia, minimo=Decimal("0.3"), maximo=Decimal("5")):
    """Descarta lecturas absurdas del modelo: entre 0.3x y 5x de la tarifa propia."""
    if precio is None or not referencia:
        return False
    return referencia * minimo <= precio <= referencia * maximo


def validar_tarifa_booking(rate):
    """
    Valida una tarifa leída de Booking: total = base + impuestos (tolerancia 1 USD).
    Devuelve (precio_total, motivo): precio_total es None si no pasa la validación.
    """
    if not isinstance(rate, dict):
        return None, "tarifa_invalida"

    base, _ = parsear_precio(rate.get("base_price"))
    total, _ = parsear_precio(rate.get("total_price"))
    impuestos_crudo = rate.get("taxes", 0)
    impuestos = (
        Decimal("0") if impuestos_crudo in (0, "0", None)
        else parsear_precio(impuestos_crudo)[0]
    )

    if total is None or base is None or impuestos is None:
        return None, "precio_no_numerico"
    if abs((base + impuestos) - total) > Decimal("1"):
        return None, "total_no_coincide_con_base_mas_impuestos"
    return total, None


# -----------------------------
# Nombres de habitación y amenities
# -----------------------------

# Mismas cosas escritas distinto en el hotel y en las plataformas.
_SINONIMOS = [("wi fi", "wifi"), ("interior", "cubierta")]


def _limpiar(texto):
    limpio = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(texto).lower())).strip()
    for origen, destino in _SINONIMOS:
        limpio = limpio.replace(origen, destino)
    return limpio


def mapear_tipo(nombre_en_plataforma, tipos, plataforma, similitud_minima=0.85):
    """
    Busca el tipo propio equivalente usando `nombres_ota` del catálogo, no el mínimo
    de cualquier habitación. Tolera pequeñas diferencias de lectura del modelo.
    """
    objetivo = _limpiar(nombre_en_plataforma or "")
    if not objetivo:
        return None

    mejor, mejor_puntaje = None, 0.0
    for tipo in tipos:
        for nombre in (tipo.get("nombres_ota") or {}).get(plataforma, []):
            puntaje = difflib.SequenceMatcher(None, objetivo, _limpiar(nombre)).ratio()
            if puntaje > mejor_puntaje:
                mejor, mejor_puntaje = tipo["id"], puntaje

    return mejor if mejor_puntaje >= similitud_minima else None


def normalizar_amenities(lista):
    """Conjunto normalizado: minúsculas, sin signos ni duplicados."""
    return sorted({_limpiar(a) for a in (lista or []) if isinstance(a, str) and _limpiar(a)})


def faltantes_en(propias, de_plataforma, similitud_minima=0.7):
    """Amenities propias que no aparecen (ni parecido) en la lista de la plataforma."""
    propias, de_plataforma = normalizar_amenities(propias), normalizar_amenities(de_plataforma)
    faltantes = []
    for amenity in propias:
        if not any(
            amenity in otra or otra in amenity
            or difflib.SequenceMatcher(None, amenity, otra).ratio() >= similitud_minima
            for otra in de_plataforma
        ):
            faltantes.append(amenity)
    return faltantes