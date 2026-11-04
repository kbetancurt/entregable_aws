"""
Pruebas locales de la Lambda de consulta (GET /habitaciones). No tocan AWS:
moto simula DynamoDB. Los datos tienen el mismo formato que scripts/seed.py.

    pip install -r requirements-dev.txt
    pytest tests/test_consulta.py -v
"""
import importlib.util
import json
import time
from datetime import date, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

HOTEL = "aloft-mvd"
ROOT = Path(__file__).resolve().parent.parent
START = date.today() + timedelta(days=2)
NIGHTS_SEEDED = 10

# Mismos tipos, capacidades, precios e inventario que scripts/seed.py.
ROOM_TYPES = [
    {"id": "KING-ALOFT", "nombre": "Habitación King con vista a la ciudad",
     "capacidad": 2, "precio_base": 145, "inventario_total": 5},
    {"id": "TWIN-TWIN", "nombre": "Habitación Twin con vista a la ciudad",
     "capacidad": 2, "precio_base": 145, "inventario_total": 3},
    {"id": "QUEEN-QUEEN-CITY", "nombre": "Habitación con dos camas Queen y vista a la ciudad",
     "capacidad": 4, "precio_base": 185, "inventario_total": 2},
    {"id": "KING-RIVER", "nombre": "Habitación King con vista al río",
     "capacidad": 2, "precio_base": 165, "inventario_total": 4},
    {"id": "QUEEN-QUEEN-RIVER", "nombre": "Habitación con dos camas Queen y vista al río",
     "capacidad": 4, "precio_base": 185, "inventario_total": 1},
]


def iso(offset):
    return (START + timedelta(days=offset)).isoformat()


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # Un .env local (p. ej. DYNAMODB_ENDPOINT de DynamoDB Local) no debe filtrarse
    # a las pruebas: se anula load_dotenv y se fijan las variables que usa la Lambda.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "")
    monkeypatch.setenv("SQS_ENDPOINT", "")
    monkeypatch.setenv("TABLE_NAME", "HotelSystemTable")
    monkeypatch.setenv("HOTEL_ID", HOTEL)


@pytest.fixture
def world(env):
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="HotelSystemTable",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        for room in ROOM_TYPES:
            table.put_item(Item={"PK": f"HOTEL#{HOTEL}", "SK": f"TIPO#{room['id']}", **room})
            for i in range(NIGHTS_SEEDED):
                table.put_item(Item={
                    "PK": f"HOTEL#{HOTEL}#TIPO#{room['id']}", "SK": f"NOCHE#{iso(i)}",
                    "fecha": iso(i), "inventario_total": room["inventario_total"],
                    "disponibles": room["inventario_total"],
                })

        yield load_module("consulta_index", "backend/consulta/index.py"), table


def set_available(table, type_id, offset, value):
    table.update_item(
        Key={"PK": f"HOTEL#{HOTEL}#TIPO#{type_id}", "SK": f"NOCHE#{iso(offset)}"},
        UpdateExpression="SET disponibles = :v",
        ExpressionAttributeValues={":v": value},
    )


def search(module, entrada=0, salida=1, huespedes=2, tipo=None):
    params = {"entrada": iso(entrada), "salida": iso(salida), "huespedes": str(huespedes)}
    if tipo:
        params["tipo"] = tipo
    response = module.lambda_handler({"queryStringParameters": params}, None)
    return response["statusCode"], json.loads(response["body"])


def offered_types(combinations):
    return {room["tipo_id"] for c in combinations for room in c["habitaciones"]}


# -----------------------------
# Combinaciones y orden
# -----------------------------

def test_dos_huespedes_ofrece_una_habitacion_por_tipo_ordenadas_por_precio(world):
    module, _ = world
    status, data = search(module, 0, 2, huespedes=2)
    assert status == 200
    combos = data["combinaciones"]

    assert data["noches"] == 2 and data["hotel_id"] == HOTEL
    assert all(c["cantidad_habitaciones"] == 1 for c in combos)
    assert offered_types(combos) == {r["id"] for r in ROOM_TYPES}
    # Orden fijado: más baratas primero (145 x 2 noches ... 185 x 2 noches).
    prices = [c["precio_total"] for c in combos]
    assert prices == sorted(prices)
    assert prices[0] == 145 * 2 and prices[-1] == 185 * 2


def test_cinco_huespedes_necesita_al_menos_dos_habitaciones(world):
    module, _ = world
    status, data = search(module, 0, 2, huespedes=5)
    combos = data["combinaciones"]

    assert status == 200 and combos
    assert all(c["cantidad_habitaciones"] >= 2 for c in combos)
    assert all(c["capacidad_total"] >= 5 for c in combos)
    # Orden fijado: la primera es la de menos habitaciones y menor precio
    # (una Queen de 185 + una King/Twin de 145, 2 noches).
    first = combos[0]
    assert first["cantidad_habitaciones"] == 2
    assert first["precio_total"] == (185 + 145) * 2


def test_el_orden_es_habitaciones_luego_precio_luego_capacidad_sobrante(world):
    module, _ = world
    _, data = search(module, 0, 2, huespedes=5)
    keys = [
        (c["cantidad_habitaciones"], c["precio_total"], c["capacidad_total"] - 5)
        for c in data["combinaciones"]
    ]
    assert keys == sorted(keys)


def test_precio_total_es_suma_de_precio_por_cantidad_por_noches(world):
    module, _ = world
    _, data = search(module, 0, 3, huespedes=5)
    for c in data["combinaciones"]:
        expected = sum(
            r["precio_por_noche"] * r["cantidad"] * data["noches"] for r in c["habitaciones"]
        )
        assert c["precio_total"] == expected
        assert c["cantidad_habitaciones"] == sum(r["cantidad"] for r in c["habitaciones"])
        assert c["capacidad_total"] == sum(
            r["capacidad_por_habitacion"] * r["cantidad"] for r in c["habitaciones"]
        )


# -----------------------------
# Disponibilidad por noche
# -----------------------------

def test_nunca_ofrece_mas_unidades_que_las_disponibles(world):
    module, _ = world
    _, data = search(module, 0, 2, huespedes=12)
    stock = {r["id"]: r["inventario_total"] for r in ROOM_TYPES}
    assert data["combinaciones"]
    for c in data["combinaciones"]:
        for room in c["habitaciones"]:
            assert room["cantidad"] <= stock[room["tipo_id"]]
    # QUEEN-QUEEN-RIVER tiene una sola unidad.
    assert all(
        room["cantidad"] <= 1
        for c in data["combinaciones"] for room in c["habitaciones"]
        if room["tipo_id"] == "QUEEN-QUEEN-RIVER"
    )


def test_noche_sin_cupo_deja_afuera_ese_tipo(world):
    module, table = world
    set_available(table, "KING-RIVER", 1, 0)
    _, data = search(module, 0, 3, huespedes=2)
    assert "KING-RIVER" not in offered_types(data["combinaciones"])
    # Para otras fechas que no tocan esa noche sí se ofrece.
    _, other = search(module, 2, 4, huespedes=2)
    assert "KING-RIVER" in offered_types(other["combinaciones"])


def test_el_cupo_es_el_minimo_de_todas_las_noches(world):
    module, table = world
    set_available(table, "KING-RIVER", 1, 1)  # noche 0: 4 unidades, noche 1: 1
    _, data = search(module, 0, 2, huespedes=4, tipo="KING-RIVER")
    # Con 4 huéspedes harían falta 2 King y solo hay 1 para toda la estadía.
    assert data["combinaciones"] == []


def test_el_dia_de_salida_no_cuenta_como_noche(world):
    module, table = world
    set_available(table, "KING-RIVER", 2, 0)  # noche del día de salida
    _, data = search(module, 0, 2, huespedes=2)  # noches 0 y 1
    assert "KING-RIVER" in offered_types(data["combinaciones"])
    _, longer = search(module, 0, 3, huespedes=2)  # ahora la noche 2 sí cuenta
    assert "KING-RIVER" not in offered_types(longer["combinaciones"])


def test_noche_sin_inventario_sembrado_cuenta_como_cero(world):
    module, _ = world
    status, data = search(module, NIGHTS_SEEDED - 1, NIGHTS_SEEDED + 2, huespedes=2)
    assert status == 200
    assert data["combinaciones"] == []


def test_sin_cupo_en_ningun_tipo_devuelve_lista_vacia(world):
    module, table = world
    for room in ROOM_TYPES:
        set_available(table, room["id"], 0, 0)
    status, data = search(module, 0, 1, huespedes=2)
    assert status == 200 and data["combinaciones"] == []


# -----------------------------
# Filtro por tipo
# -----------------------------

def test_filtra_por_tipo(world):
    module, _ = world
    status, data = search(module, 0, 2, huespedes=4, tipo="KING-RIVER")
    assert status == 200
    assert offered_types(data["combinaciones"]) == {"KING-RIVER"}
    assert data["combinaciones"][0]["habitaciones"][0]["cantidad"] == 2  # 2 King para 4


def test_tipo_inexistente_es_400(world):
    module, _ = world
    status, data = search(module, 0, 2, tipo="NOEXISTE")
    assert status == 400 and "NOEXISTE" in data["error"]


# -----------------------------
# Validación de parámetros
# -----------------------------

@pytest.mark.parametrize("params", [
    None,
    {},
    {"entrada": "2030-01-01", "salida": "2030-01-03"},                       # falta huespedes
    {"entrada": "no-es-fecha", "salida": "2030-01-03", "huespedes": "2"},
    {"entrada": "2030-01-03", "salida": "2030-01-03", "huespedes": "2"},     # misma fecha
    {"entrada": "2030-01-05", "salida": "2030-01-03", "huespedes": "2"},     # salida antes
    {"entrada": "2030-01-01", "salida": "2030-01-03", "huespedes": "0"},
    {"entrada": "2030-01-01", "salida": "2030-01-03", "huespedes": "abc"},
])
def test_parametros_invalidos_son_400(world, params):
    module, _ = world
    response = module.lambda_handler({"queryStringParameters": params}, None)
    assert response["statusCode"] == 400
    assert "error" in json.loads(response["body"])


def test_error_interno_no_expone_detalles(world, monkeypatch):
    module, _ = world

    def boom(*args, **kwargs):
        raise RuntimeError("detalle secreto")

    monkeypatch.setattr(module, "get_available_room_types", boom)
    response = module.lambda_handler(
        {"queryStringParameters": {"entrada": iso(0), "salida": iso(1), "huespedes": "2"}}, None
    )
    assert response["statusCode"] == 500
    assert "secreto" not in response["body"]


def test_la_respuesta_incluye_cors(world):
    module, _ = world
    response = module.lambda_handler(
        {"queryStringParameters": {"entrada": iso(0), "salida": iso(1), "huespedes": "2"}}, None
    )
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"


# -----------------------------
# Coherencia con la Lambda de reserva
# -----------------------------

def test_lo_que_consulta_ofrece_reserva_lo_acepta_y_luego_ya_no_se_ofrece(world):
    """Las dos Lambdas deben coincidir: consulta ofrece -> reserva confirma."""
    module, _ = world
    reserva = load_module("reserva_index", "backend/reserva/index.py")

    _, data = search(module, 0, 2, huespedes=5)
    combo = data["combinaciones"][0]
    rooms = [{"tipo_id": r["tipo_id"], "cantidad": r["cantidad"]} for r in combo["habitaciones"]]

    event = {
        "body": json.dumps({"entrada": iso(0), "salida": iso(2), "huespedes": 5, "habitaciones": rooms}),
        "headers": {},
        "requestContext": {"authorizer": {"claims": {"sub": "user-1"}}},
    }
    response = reserva.lambda_handler(event, None)
    assert response["statusCode"] == 201
    assert json.loads(response["body"])["reserva"]["precio_total"] == combo["precio_total"]

    # Se agota QUEEN-QUEEN-RIVER (1 unidad) si la combinación la usaba.
    if any(r["tipo_id"] == "QUEEN-QUEEN-RIVER" for r in combo["habitaciones"]):
        _, after = search(module, 0, 2, huespedes=4)
        assert "QUEEN-QUEEN-RIVER" not in offered_types(after["combinaciones"])


# -----------------------------
# Rendimiento de la búsqueda de combinaciones
# -----------------------------

def test_inventario_grande_responde_en_tiempo_razonable(world):
    module, table = world
    for room in ROOM_TYPES:
        for i in range(2):
            set_available(table, room["id"], i, 8)
    start = time.perf_counter()
    status, data = search(module, 0, 2, huespedes=20)
    elapsed = time.perf_counter() - start

    assert status == 200 and data["combinaciones"]
    assert elapsed < 5, f"la búsqueda de combinaciones tardó {elapsed:.1f}s"