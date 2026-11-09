"""
Pruebas locales de la Lambda de avisos (consume SQS, guarda alertas y envía por WebSocket).
moto simula DynamoDB; el cliente de WebSocket es un doble que registra los envíos.

    pytest tests/test_avisos.py -v
"""
import importlib.util
import json
import time
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

HOTEL = "aloft-mvd"
ROOT = Path(__file__).resolve().parent.parent


class FakeWebSocket:
    def __init__(self):
        self.sent = []          # (connection_id, evento)
        self.gone = set()       # conexiones que ya no existen (410)
        self.broken = set()     # conexiones que fallan con otro error

    def post_to_connection(self, ConnectionId, Data):
        if ConnectionId in self.gone:
            raise ClientError({"Error": {"Code": "GoneException"}}, "PostToConnection")
        if ConnectionId in self.broken:
            raise ClientError({"Error": {"Code": "LimitExceededException"}}, "PostToConnection")
        self.sent.append((ConnectionId, json.loads(Data)))


@pytest.fixture
def world(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1", "TABLE_NAME": "T", "HOTEL_ID": HOTEL,
        "DYNAMODB_ENDPOINT": "", "WS_ENDPOINT": "https://ws.test/prod",
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
        spec = importlib.util.spec_from_file_location("avisos_index", ROOT / "backend/avisos/index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fake = FakeWebSocket()
        module._ws_client = fake
        yield module, table, fake


def connect(table, connection_id, rol="admin", usuario_id="", hotel=HOTEL):
    table.put_item(Item={
        "PK": f"HOTEL#{hotel}#CONN", "SK": f"CONN#{connection_id}",
        "connection_id": connection_id, "rol": rol, "usuario_id": usuario_id,
    })


def make_event(n=1, tipo="PRECIO_MENOR_EN_PLATAFORMA", **extra):
    return {
        "eventId": f"ev{n}", "tipo": tipo, "hotelId": HOTEL,
        "ocurridoEn": f"2026-11-09T00:{n:02d}:00+00:00", "mensaje": f"aviso {n}", **extra,
    }


def sqs_batch(*events):
    return {"Records": [{"messageId": f"m{i}", "body": json.dumps(e) if isinstance(e, dict) else e}
                        for i, e in enumerate(events)]}


def alerts(table):
    return [i for i in table.scan()["Items"] if i["SK"].startswith("ALERTA#")]


# ---------------- destinatarios ----------------

def test_el_evento_llega_a_los_admins_conectados_y_no_a_los_clientes(world):
    module, table, ws = world
    connect(table, "c-admin1"); connect(table, "c-admin2")
    connect(table, "c-cliente", rol="cliente", usuario_id="u9")

    result = module.lambda_handler(sqs_batch(make_event()), None)

    assert result == {"batchItemFailures": []}
    assert sorted(c for c, _ in ws.sent) == ["c-admin1", "c-admin2"]
    assert ws.sent[0][1]["tipo"] == "PRECIO_MENOR_EN_PLATAFORMA"


def test_el_cliente_dueno_recibe_solo_los_eventos_con_su_usuario(world):
    module, table, ws = world
    connect(table, "c-admin"); connect(table, "c-dueno", rol="cliente", usuario_id="u1")
    connect(table, "c-otro", rol="cliente", usuario_id="u2")

    module.lambda_handler(sqs_batch(make_event(1, "RESERVA_CREADA", usuarioId="u1")), None)

    assert sorted(c for c, _ in ws.sent) == ["c-admin", "c-dueno"]


def test_no_envia_a_conexiones_de_otro_hotel(world):
    module, table, ws = world
    connect(table, "c-otro-hotel", hotel="otro-hotel")
    module.lambda_handler(sqs_batch(make_event()), None)
    assert ws.sent == []


# ---------------- alertas guardadas ----------------

def test_sin_ningun_admin_conectado_la_alerta_igual_queda_guardada(world):
    module, table, ws = world
    result = module.lambda_handler(sqs_batch(make_event(1, "AMENITIES_DISTINTAS", faltantes=["sauna"])), None)

    assert result == {"batchItemFailures": []} and ws.sent == []
    [alert] = alerts(table)
    assert alert["SK"] == "ALERTA#2026-11-09T00:01:00+00:00#ev1"
    assert alert["leida"] is False and alert["faltantes"] == ["sauna"]


def test_la_alerta_tiene_ttl_para_que_no_crezca_sin_limite(world):
    module, table, _ = world
    module.lambda_handler(sqs_batch(make_event()), None)
    [alert] = alerts(table)
    dias = (int(alert["ttl"]) - time.time()) / 86400
    assert 29 < dias <= 30


def test_decimales_del_scraper_se_guardan_como_numeros_de_dynamodb(world):
    module, table, _ = world
    module.lambda_handler(sqs_batch(make_event(precioPlataforma=309.5, diferenciaPct=-28.1)), None)
    [alert] = alerts(table)
    assert float(alert["precioPlataforma"]) == 309.5


def test_reserva_creada_no_se_guarda_como_alerta_del_panel(world):
    module, table, ws = world
    connect(table, "c-dueno", rol="cliente", usuario_id="u1")
    module.lambda_handler(sqs_batch(make_event(1, "RESERVA_CREADA", usuarioId="u1")), None)
    assert alerts(table) == [] and len(ws.sent) == 1


# ---------------- idempotencia y fallos ----------------

def test_evento_repetido_no_se_envia_ni_se_guarda_dos_veces(world):
    module, table, ws = world
    connect(table, "c-admin")
    event = make_event()
    module.lambda_handler(sqs_batch(event, event), None)
    module.lambda_handler(sqs_batch(event), None)

    assert len(ws.sent) == 1 and len(alerts(table)) == 1


def test_conexion_caida_410_se_borra_y_no_falla_el_evento(world):
    module, table, ws = world
    connect(table, "c-viva"); connect(table, "c-muerta")
    ws.gone.add("c-muerta")

    result = module.lambda_handler(sqs_batch(make_event()), None)

    assert result == {"batchItemFailures": []}
    assert [c for c, _ in ws.sent] == ["c-viva"]
    assert "Item" not in table.get_item(Key={"PK": f"HOTEL#{HOTEL}#CONN", "SK": "CONN#c-muerta"})
    assert "Item" in table.get_item(Key={"PK": f"HOTEL#{HOTEL}#CONN", "SK": "CONN#c-viva"})


def test_error_de_envio_reporta_el_mensaje_y_el_reintento_lo_procesa(world):
    module, table, ws = world
    connect(table, "c-admin")
    ws.broken.add("c-admin")

    result = module.lambda_handler(sqs_batch(make_event()), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m0"}]}

    ws.broken.clear()                      # SQS reentrega el mismo mensaje
    result = module.lambda_handler(sqs_batch(make_event()), None)
    assert result == {"batchItemFailures": []} and len(ws.sent) == 1
    assert len(alerts(table)) == 1         # el reintento no duplica la alerta


def test_un_mensaje_fallido_no_arrastra_a_los_demas_del_lote(world):
    module, table, ws = world
    connect(table, "c-admin")
    ws.broken.add("c-admin")
    result = module.lambda_handler(sqs_batch(make_event(1), make_event(2)), None)
    assert [f["itemIdentifier"] for f in result["batchItemFailures"]] == ["m0", "m1"]

    ws.broken.clear()                      # reentrega del primero
    assert module.lambda_handler(sqs_batch(make_event(1)), None) == {"batchItemFailures": []}
    assert len(ws.sent) == 1


@pytest.mark.parametrize("body", [
    "esto no es json", json.dumps({"tipo": "X"}), json.dumps({"eventId": "e"}), json.dumps({}),
])
def test_mensajes_invalidos_se_descartan_sin_reintentar(world, body):
    module, table, ws = world
    result = module.lambda_handler(sqs_batch(body), None)
    assert result == {"batchItemFailures": []}
    assert ws.sent == [] and alerts(table) == []
