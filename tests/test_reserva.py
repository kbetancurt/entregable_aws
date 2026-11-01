"""
Pruebas locales de la Lambda de reserva. No tocan AWS: moto simula DynamoDB y SQS.

    pip install -r requirements-dev.txt
    pytest tests -v
"""
import importlib.util
import json
import os
import threading
from datetime import date, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

HOTEL = "aloft-mvd"
ROOT = Path(__file__).resolve().parent.parent
TOMORROW = date.today() + timedelta(days=2)


def iso(offset):
    return (TOMORROW + timedelta(days=offset)).isoformat()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("DYNAMODB_ENDPOINT", raising=False)
    monkeypatch.delenv("SQS_ENDPOINT", raising=False)


@pytest.fixture
def world(env, monkeypatch):
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
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
        types = {
            "KING-RIVER": dict(capacidad=2, precio_base=165, total=2),
            "QUEEN-QUEEN-RIVER": dict(capacidad=4, precio_base=185, total=1),
        }
        for type_id, t in types.items():
            table.put_item(Item={
                "PK": f"HOTEL#{HOTEL}", "SK": f"TIPO#{type_id}", "id": type_id,
                "capacidad": t["capacidad"], "precio_base": t["precio_base"],
                "inventario_total": t["total"],
            })
            for i in range(10):
                table.put_item(Item={
                    "PK": f"HOTEL#{HOTEL}#TIPO#{type_id}", "SK": f"NOCHE#{iso(i)}",
                    "fecha": iso(i), "inventario_total": t["total"],
                    "disponibles": t["total"],
                })

        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="eventos")["QueueUrl"]
        monkeypatch.setenv("EVENTS_QUEUE_URL", queue_url)

        spec = importlib.util.spec_from_file_location(
            "reserva_index", ROOT / "backend/reserva/index.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        yield module, table, sqs, queue_url


def request(body, sub="user-1", headers=None):
    event = {
        "body": json.dumps(body) if body is not None else None,
        "headers": headers or {},
        "requestContext": {"authorizer": {"claims": {"sub": sub}}} if sub else {},
    }
    return event


def book(module, rooms, a=0, b=2, sub="user-1", **extra):
    body = {"entrada": iso(a), "salida": iso(b), "habitaciones": rooms, **extra}
    resp = module.lambda_handler(request(body, sub=sub), None)
    return resp["statusCode"], json.loads(resp["body"])


def available(table, type_id, offset):
    item = table.get_item(Key={
        "PK": f"HOTEL#{HOTEL}#TIPO#{type_id}", "SK": f"NOCHE#{iso(offset)}"
    })["Item"]
    return int(item["disponibles"])


def events(sqs, queue_url):
    out = []
    while True:
        msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
        if not msgs:
            return out
        for m in msgs:
            out.append(json.loads(m["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])


def test_reserva_ok_descuenta_cada_noche_y_calcula_precio(world):
    module, table, sqs, q = world
    status, data = book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}], 0, 3)
    assert status == 201
    assert data["reserva"]["precio_total"] == 165 * 3
    assert data["reserva"]["estado"] == "CONFIRMADA"
    assert data["reserva"]["usuario_id"] == "user-1"
    assert [available(table, "KING-RIVER", i) for i in range(4)] == [1, 1, 1, 2]  # la salida no cuenta


def test_reserva_normal_no_publica_eventos(world):
    module, table, sqs, q = world
    book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}])
    assert events(sqs, q) == []


def test_capacidad_agotada(world):
    module, table, sqs, q = world
    book(module, [{"tipo_id": "QUEEN-QUEEN-RIVER", "cantidad": 1}], 0, 2)
    evs = events(sqs, q)
    assert [e["tipo"] for e in evs] == ["CAPACIDAD_AGOTADA"]
    assert evs[0]["hotelId"] == HOTEL and evs[0]["eventId"]
    assert evs[0]["tipoHabitacion"] == "QUEEN-QUEEN-RIVER"


def test_sin_disponibilidad_409_y_no_descuenta_nada(world):
    module, table, sqs, q = world
    assert book(module, [{"tipo_id": "QUEEN-QUEEN-RIVER", "cantidad": 1}], 1, 2)[0] == 201
    # Estadía noche 0..2: la noche 1 ya no tiene cupo -> toda la transacción falla.
    status, _ = book(module, [{"tipo_id": "QUEEN-QUEEN-RIVER", "cantidad": 1}], 0, 3)
    assert status == 409
    assert available(table, "QUEEN-QUEEN-RIVER", 0) == 1
    assert available(table, "QUEEN-QUEEN-RIVER", 2) == 1


def test_cantidad_mayor_al_inventario(world):
    module, *_ = world
    assert book(module, [{"tipo_id": "KING-RIVER", "cantidad": 3}])[0] == 409


def test_noche_sin_inventario_sembrado_es_409(world):
    module, *_ = world
    assert book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}], 8, 12)[0] == 409


def test_sobreventa_concurrente(world):
    """10 clientes a la vez por 1 sola habitación: gana exactamente uno."""
    module, table, *_ = world
    results = []

    def worker(i):
        results.append(book(module, [{"tipo_id": "QUEEN-QUEEN-RIVER", "cantidad": 1}], 0, 2, sub=f"u{i}")[0])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(results) == [201] + [409] * 9
    assert available(table, "QUEEN-QUEEN-RIVER", 0) == 0
    assert available(table, "QUEEN-QUEEN-RIVER", 1) == 0


def test_requiere_usuario_del_token(world):
    module, *_ = world
    status, _ = book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}], sub=None)
    assert status == 401


def test_usuario_no_se_toma_del_cuerpo(world):
    module, *_ = world
    status, data = book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}], usuario_id="otro", precio_total=1)
    assert status == 201
    assert data["reserva"]["usuario_id"] == "user-1"
    assert data["reserva"]["precio_total"] == 330


def test_idempotency_key_no_duplica(world):
    module, table, *_ = world
    body = {"entrada": iso(0), "salida": iso(2), "habitaciones": [{"tipo_id": "KING-RIVER", "cantidad": 1}]}
    h = {"Idempotency-Key": "abc"}
    r1 = module.lambda_handler(request(body, headers=h), None)
    r2 = module.lambda_handler(request(body, headers=h), None)
    assert (r1["statusCode"], r2["statusCode"]) == (201, 200)
    assert json.loads(r1["body"])["reserva"]["reserva_id"] == json.loads(r2["body"])["reserva"]["reserva_id"]
    assert available(table, "KING-RIVER", 0) == 1  # descontó una sola vez


@pytest.mark.parametrize("mod", [
    {"entrada": "2020-01-01", "salida": "2020-01-03"},  # pasado
    {"salida": "2000-01-01"},                           # salida <= entrada
    {"entrada": "no-es-fecha"},
    {"habitaciones": []},
    {"habitaciones": [{"tipo_id": "NOEXISTE", "cantidad": 1}]},
    {"habitaciones": [{"tipo_id": "KING-RIVER", "cantidad": 0}]},
    {"habitaciones": [{"tipo_id": "KING-RIVER", "cantidad": "1"}]},
    {"huespedes": 5},                                   # no caben en 1 King
    {"salida": (TOMORROW + timedelta(days=60)).isoformat()},  # > 30 noches
])
def test_validaciones_400(world, mod):
    module, table, *_ = world
    body = {"entrada": iso(0), "salida": iso(2), "habitaciones": [{"tipo_id": "KING-RIVER", "cantidad": 1}], **mod}
    resp = module.lambda_handler(request(body), None)
    assert resp["statusCode"] == 400
    assert available(table, "KING-RIVER", 0) == 2


def test_json_invalido_y_cuerpo_vacio(world):
    module, *_ = world
    assert module.lambda_handler({"body": "{no", "requestContext": {"authorizer": {"claims": {"sub": "u"}}}}, None)["statusCode"] == 400
    assert module.lambda_handler(request(None), None)["statusCode"] == 400


def test_mismo_tipo_repetido_se_acumula(world):
    module, table, *_ = world
    status, _ = book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}, {"tipo_id": "KING-RIVER", "cantidad": 1}])
    assert status == 201
    assert available(table, "KING-RIVER", 0) == 0


def test_falla_de_sqs_no_revierte_la_reserva(world, monkeypatch):
    module, table, *_ = world
    monkeypatch.setattr(module, "EVENTS_QUEUE_URL", "http://localhost:1/inexistente")
    status, _ = book(module, [{"tipo_id": "KING-RIVER", "cantidad": 1}])
    assert status == 201
