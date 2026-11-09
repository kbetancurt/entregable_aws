"""
Pruebas locales de la Lambda de alertas (GET /alertas y POST /alertas/leer) con moto.

    pytest tests/test_alertas.py -v
"""
import importlib.util
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

HOTEL = "aloft-mvd"
ROOT = Path(__file__).resolve().parent.parent


def load_alertas():
    spec = importlib.util.spec_from_file_location("alertas_index", ROOT / "backend/alertas/index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def world(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1", "TABLE_NAME": "T", "HOTEL_ID": HOTEL, "DYNAMODB_ENDPOINT": "",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="T",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield load_alertas(), table


def put_alert(table, n, leida=False, hotel=HOTEL, tipo="PRECIO_MENOR_EN_PLATAFORMA"):
    ts = f"2026-11-09T00:{n:02d}:00+00:00"
    table.put_item(Item={
        "PK": f"HOTEL#{hotel}", "SK": f"ALERTA#{ts}#ev{n}", "eventId": f"ev{n}", "tipo": tipo,
        "ocurridoEn": ts, "mensaje": f"alerta {n}", "leida": leida,
    })
    return f"ALERTA#{ts}#ev{n}"


def call(module, method, path, groups="admin", body=None, query=None):
    event = {
        "httpMethod": method, "path": path,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": {"sub": "u1", "cognito:groups": groups}}},
    }
    response = module.lambda_handler(event, None)
    return response["statusCode"], json.loads(response["body"])


# ---------------- permisos ----------------

@pytest.mark.parametrize("groups", ["", "cliente", "[cliente]", "administrador"])
def test_solo_admin_puede_ver(world, groups):
    module, _ = world
    status, data = call(module, "GET", "/alertas", groups=groups)
    assert status == 403 and "administradores" in data["error"]


@pytest.mark.parametrize("groups", ["admin", "[admin]", "[cliente admin]", "cliente admin"])
def test_admin_en_los_formatos_de_api_gateway(world, groups):
    module, _ = world
    assert call(module, "GET", "/alertas", groups=groups)[0] == 200


def test_sin_claims_es_403(world):
    module, _ = world
    response = module.lambda_handler({"httpMethod": "GET", "path": "/alertas"}, None)
    assert response["statusCode"] == 403


# ---------------- GET /alertas ----------------

def test_lista_vacia(world):
    module, _ = world
    status, data = call(module, "GET", "/alertas")
    assert status == 200 and data == {"alertas": [], "no_leidas": 0}


def test_lista_las_mas_nuevas_primero_con_id_y_sin_claves_internas(world):
    module, table = world
    for n in (1, 2, 3):
        put_alert(table, n, leida=(n == 1))
    _, data = call(module, "GET", "/alertas")

    assert [a["eventId"] for a in data["alertas"]] == ["ev3", "ev2", "ev1"]
    assert data["no_leidas"] == 2
    primera = data["alertas"][0]
    assert primera["id"].startswith("ALERTA#") and "PK" not in primera and "SK" not in primera


def test_no_mezcla_alertas_de_otro_hotel(world):
    module, table = world
    put_alert(table, 1)
    put_alert(table, 2, hotel="otro-hotel")
    _, data = call(module, "GET", "/alertas")
    assert [a["eventId"] for a in data["alertas"]] == ["ev1"]


def test_limite_y_validacion(world):
    module, table = world
    for n in range(1, 6):
        put_alert(table, n)
    assert len(call(module, "GET", "/alertas", query={"limite": "2"})[1]["alertas"]) == 2
    for malo in ("0", "101", "abc"):
        assert call(module, "GET", "/alertas", query={"limite": malo})[0] == 400


def test_alerta_sin_campo_leida_cuenta_como_no_leida(world):
    module, table = world
    table.put_item(Item={"PK": f"HOTEL#{HOTEL}", "SK": "ALERTA#2026-11-09T00:00:00+00:00#x", "tipo": "X"})
    _, data = call(module, "GET", "/alertas")
    assert data["no_leidas"] == 1 and data["alertas"][0]["leida"] is False


def test_decimales_se_serializan_como_numeros(world):
    module, table = world
    table.put_item(Item={"PK": f"HOTEL#{HOTEL}", "SK": "ALERTA#2026-11-09T00:00:00+00:00#x",
                         "tipo": "X", "precioPlataforma": __import__("decimal").Decimal("309.5")})
    assert call(module, "GET", "/alertas")[1]["alertas"][0]["precioPlataforma"] == 309.5


# ---------------- POST /alertas/leer ----------------

def test_marcar_algunas_como_leidas(world):
    module, table = world
    ids = [put_alert(table, n) for n in (1, 2, 3)]
    status, data = call(module, "POST", "/alertas/leer", body={"ids": ids[:2]})
    assert status == 200 and data == {"marcadas": 2}

    _, listado = call(module, "GET", "/alertas")
    assert listado["no_leidas"] == 1
    assert [a["leida"] for a in listado["alertas"]] == [False, True, True]


def test_marcar_todas(world):
    module, table = world
    for n in (1, 2, 3):
        put_alert(table, n, leida=(n == 2))
    status, data = call(module, "POST", "/alertas/leer", body={"todas": True})
    assert status == 200 and data == {"marcadas": 2}
    assert call(module, "GET", "/alertas")[1]["no_leidas"] == 0


def test_marcar_un_id_inexistente_se_ignora_y_no_crea_nada(world):
    module, table = world
    status, data = call(module, "POST", "/alertas/leer", body={"ids": ["ALERTA#no-existe"]})
    assert status == 200 and data == {"marcadas": 0}
    assert table.scan()["Count"] == 0


def test_no_marca_alertas_de_otro_hotel(world):
    module, table = world
    put_alert(table, 1, hotel="otro-hotel")
    status, data = call(module, "POST", "/alertas/leer", body={"todas": True})
    assert data == {"marcadas": 0}
    assert table.get_item(Key={"PK": "HOTEL#otro-hotel", "SK": "ALERTA#2026-11-09T00:01:00+00:00#ev1"})["Item"]["leida"] is False


@pytest.mark.parametrize("body", [
    {}, {"ids": []}, {"ids": "x"}, {"ids": [1]}, {"ids": ["TARIFA#KING#2026-11-20"]},
    {"ids": ["ALERTA#x"] * 101}, {"todas": "si"},
])
def test_pedidos_invalidos_son_400(world, body):
    module, _ = world
    assert call(module, "POST", "/alertas/leer", body=body)[0] == 400


def test_cuerpo_que_no_es_json_es_400(world):
    module, _ = world
    event = {"httpMethod": "POST", "path": "/alertas/leer", "body": "{no json",
             "requestContext": {"authorizer": {"claims": {"cognito:groups": "admin"}}}}
    assert module.lambda_handler(event, None)["statusCode"] == 400


def test_ruta_desconocida_es_404(world):
    module, _ = world
    assert call(module, "GET", "/otra")[0] == 404
    assert call(module, "POST", "/alertas")[0] == 404
