"""
Pruebas locales del scraper. Sin red ni Playwright: las funciones puras se prueban solas
y el handler corre en modo demo contra moto (DynamoDB + SQS simulados).

    pytest tests/test_scraper.py -v
"""
import importlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

SCRAPER = Path(__file__).resolve().parent.parent / "backend" / "scraper"
sys.path.insert(0, str(SCRAPER))

import comparar  # noqa: E402
import normalizar  # noqa: E402

D = Decimal
HOTEL = "aloft-mvd"
NOCHE = "2026-12-01"


# ---------------- normalizar ----------------

@pytest.mark.parametrize("texto,esperado", [
    ("UYU 8,000", (D("8000"), "UYU")),
    ("US$506", (D("506"), "USD")),
    ("$ 145.50", (D("145.50"), "USD")),
    ("1.234,56 EUR", (D("1234.56"), "EUR")),
    (145, (D("145"), None)),
])
def test_parsear_precio(texto, esperado):
    assert normalizar.parsear_precio(texto) == esperado


@pytest.mark.parametrize("valor", [None, "", "gratis", 0, "0", True])
def test_parsear_precio_invalido_nunca_es_cero(valor):
    assert normalizar.parsear_precio(valor) == (None, None)


def test_a_usd_no_inventa_tipo_de_cambio():
    assert normalizar.a_usd(D("8000"), "UYU", {}) is None
    assert normalizar.a_usd(D("8000"), "UYU", {"UYU": D("40")}) == D("200.00")
    assert normalizar.a_usd(D("100"), None) == D("100")


def test_validar_tarifa_booking():
    assert normalizar.validar_tarifa_booking(
        {"base_price": 114, "taxes": 25, "total_price": 139})[0] == D("139")
    assert normalizar.validar_tarifa_booking(
        {"base_price": 114, "taxes": 25, "total_price": 200})[0] is None
    assert normalizar.validar_tarifa_booking("basura")[0] is None


TIPOS = [{"id": "KING-ALOFT", "precio_base": 145,
          "nombres_ota": {"Booking.com": ["ALOFT, Guest room, 1 King, City view"]}}]


def test_mapear_tipo():
    assert normalizar.mapear_tipo("Aloft, Guest room, 1 King, City View", TIPOS, "Booking.com") == "KING-ALOFT"
    assert normalizar.mapear_tipo("Suite presidencial", TIPOS, "Booking.com") is None


def test_precio_razonable():
    assert normalizar.precio_razonable(D("139"), D("145"))
    assert not normalizar.precio_razonable(D("5"), D("145"))
    assert not normalizar.precio_razonable(D("5000"), D("145"))


# ---------------- comparar ----------------

def test_precio_menor_solo_cuando_pasa_a_ser_mas_barato():
    ev, estado = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", D("139"), D("145"), None)
    assert [e["tipo"] for e in ev] == ["PRECIO_MENOR_EN_PLATAFORMA"]
    assert estado["menor"] is True
    # Misma situación en la ejecución siguiente: sin alerta repetida.
    ev, _ = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", D("139"), D("145"), estado)
    assert ev == []


def test_menos_del_umbral_no_alerta():
    ev, estado = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", D("144"), D("145"), None)
    assert ev == [] and estado["menor"] is False


def test_cambio_precio_con_umbral():
    previo = {"precio": D("145"), "menor": False}
    ev, _ = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", D("170"), D("145"), previo)
    assert [e["tipo"] for e in ev] == ["CAMBIO_PRECIO"]
    assert ev[0]["variacionPct"] == 17.2
    ev, _ = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", D("150"), D("145"), previo)
    assert ev == []


def test_sin_precio_no_alerta_ni_guarda_cero():
    ev, estado = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", None, D("145"), None)
    assert ev == [] and estado == {"precio": None, "disponible": False, "menor": False}


def test_amenities_informa_faltantes_y_luego_calla():
    hotel = ["Wi-Fi", "Piscina interior", "Bar W XYZ", "Sauna"]
    visibles = ["Wi-Fi gratis", "Piscina cubierta"]
    ev, estado = comparar.evaluar_amenities("Booking.com", visibles, hotel, None)
    assert ev["tipo"] == "AMENITIES_DISTINTAS" and ev["faltantes"] == ["bar w xyz", "sauna"]
    assert "2 amenities" in ev["mensaje"]
    ev, _ = comparar.evaluar_amenities("Booking.com", visibles, hotel, estado)
    assert ev is None


def test_amenities_lista_larga_o_corta_de_la_plataforma_no_es_un_cambio():
    hotel = ["Wi-Fi", "Piscina interior"]
    corta = ["wifi gratis", "piscina cubierta"]
    larga = corta + [f"servicio {i}" for i in range(70)]
    _, estado = comparar.evaluar_amenities("Booking.com", corta, hotel, None)
    ev, estado2 = comparar.evaluar_amenities("Booking.com", larga, hotel, estado)
    assert ev is None and estado2["hash"] != estado["hash"]


def test_amenities_nuevo_faltante_vuelve_a_avisar():
    hotel = ["Wi-Fi", "Sauna"]
    _, estado = comparar.evaluar_amenities("Booking.com", ["wifi", "sauna"], hotel, None)
    ev, _ = comparar.evaluar_amenities("Booking.com", ["wifi"], hotel, estado)
    assert ev and ev["faltantes"] == ["sauna"]


def test_amenities_mensaje_corto_con_muchos_faltantes():
    hotel = [f"servicio {c}" for c in "abcdefghij"]
    ev, _ = comparar.evaluar_amenities("Booking.com", ["otra cosa"], hotel, None)
    assert "y 5 más" in ev["mensaje"] and len(ev["mensaje"]) < 200


def test_amenities_vacias_es_lectura_fallida():
    ev, estado = comparar.evaluar_amenities("Booking.com", [], ["Wi-Fi"], None)
    assert ev is None and estado is None


def test_mensaje_de_precio_sin_decimales_falsos():
    ev, _ = comparar.evaluar_precio("Booking.com", NOCHE, "KING-ALOFT", D("309"), D("430"), None)
    assert "USD 309 " in ev[0]["mensaje"] and "USD 430)" in ev[0]["mensaje"]
    assert ".0" not in ev[0]["mensaje"]


# ---------------- handler (modo demo) ----------------

@pytest.fixture
def mundo(monkeypatch):
    for clave, valor in {
        "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1", "TABLE_NAME": "T", "HOTEL_ID": HOTEL,
        "SCRAPER_MODO": "demo", "DYNAMODB_ENDPOINT": "", "SQS_ENDPOINT": "",
    }.items():
        monkeypatch.setenv(clave, valor)
    with mock_aws():
        db = boto3.resource("dynamodb")
        tabla = db.create_table(
            TableName="T",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sqs = boto3.client("sqs")
        url = sqs.create_queue(QueueName="q")["QueueUrl"]
        monkeypatch.setenv("EVENTS_QUEUE_URL", url)

        nombres = {
            "KING-ALOFT": 145, "TWIN-TWIN": 145, "QUEEN-QUEEN-CITY": 185,
            "KING-RIVER": 165, "QUEEN-QUEEN-RIVER": 185,
        }
        booking = {
            "KING-ALOFT": "ALOFT, Guest room, 1 King, City view",
            "TWIN-TWIN": "Aloft, Guest room, 2 Twin/Single Bed(s), City view",
            "QUEEN-QUEEN-CITY": "ALOFT, Guest room, 2 Queen, City view",
            "KING-RIVER": "River View, Guest room, 1 King",
            "QUEEN-QUEEN-RIVER": "River View, Guest room, 2 Queen",
        }
        for tid, precio in nombres.items():
            tabla.put_item(Item={"PK": f"HOTEL#{HOTEL}", "SK": f"TIPO#{tid}", "id": tid,
                                 "precio_base": precio, "nombres_ota": {"Booking.com": [booking[tid]]}})
        tabla.put_item(Item={"PK": f"HOTEL#{HOTEL}", "SK": "HOTEL#METADATA",
                             "amenities": ["Wi-Fi", "Piscina interior", "Gimnasio Re:Charge",
                                           "Restaurante Nook", "Bar W XYZ"]})

        for nombre in ("almacen", "index"):
            sys.modules.pop(nombre, None)
        index = importlib.import_module("index")
        yield index, sqs, url
        for nombre in ("almacen", "index"):
            sys.modules.pop(nombre, None)


def eventos(sqs, url):
    salida = []
    while True:
        r = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)
        if not r.get("Messages"):
            return salida
        for m in r["Messages"]:
            salida.append(json.loads(m["Body"]))
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])


def test_demo_primera_ejecucion_publica_y_segunda_calla(mundo):
    index, sqs, url = mundo
    r = index.lambda_handler({"noches": [NOCHE]}, None)
    tipos = sorted(e["tipo"] for e in eventos(sqs, url))
    assert "PRECIO_MENOR_EN_PLATAFORMA" in tipos and "AMENITIES_DISTINTAS" in tipos
    assert "CAMBIO_PRECIO" not in tipos
    assert r["eventos"] == len(tipos) and r["fuentes_sin_datos"] == []

    index.lambda_handler({"noches": [NOCHE]}, None)
    assert eventos(sqs, url) == []


def test_demo_cambio_de_precio_publica_cambio(mundo, tmp_path, monkeypatch):
    index, sqs, url = mundo
    index.lambda_handler({"noches": [NOCHE]}, None)
    eventos(sqs, url)

    datos = json.loads((SCRAPER / "demo" / "booking.json").read_text(encoding="utf-8"))
    datos["results"]["rates"][0].update(base_price=140, taxes=30, total_price=170)
    (tmp_path / "booking.json").write_text(json.dumps(datos), encoding="utf-8")
    (tmp_path / "google_travel.json").write_text(
        (SCRAPER / "demo" / "google_travel.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(index, "DEMO_DIR", tmp_path)

    index.lambda_handler({"noches": [NOCHE]}, None)
    cambios = [e for e in eventos(sqs, url) if e["tipo"] == "CAMBIO_PRECIO"]
    assert len(cambios) == 1 and cambios[0]["tipoHabitacion"] == "KING-ALOFT"
    assert cambios[0]["precioNuevo"] == 170


def test_modo_desconocido_falla(mundo):
    index, _, _ = mundo
    with pytest.raises(ValueError):
        index.lambda_handler({"modo": "otro"}, None)


def test_una_sola_fuente(mundo):
    index, sqs, url = mundo
    r = index.lambda_handler({"noches": [NOCHE], "fuentes": ["google"]}, None)
    assert all(e["plataforma"].startswith("Google Travel") for e in eventos(sqs, url))
    assert r["observaciones"] == 2   # Expedia y Booking.com; Marriott.com es el propio hotel
    with pytest.raises(ValueError):
        index.lambda_handler({"fuentes": ["otra"]}, None)
