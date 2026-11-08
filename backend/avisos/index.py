"""
Lambda Avisos (secciones 3, 4.2, 4.3, 6.4 y 6.6).

Consume la cola de eventos (reservas y scraper), busca las conexiones
WebSocket destinatarias y les envía el evento con PostToConnection.

- Entrega al menos una vez: cada evento trae `eventId` y se marca como
  procesado con una escritura condicional; un repetido se descarta.
- Fallos parciales: devuelve batchItemFailures (ReportBatchItemFailures) para
  que SQS reintente solo los mensajes que fallaron; tras varios intentos pasan
  a la DLQ.
- 410 Gone: la conexión ya no existe y se borra de la tabla.
- Los eventos para el administrador quedan además guardados como alerta,
  para que el panel los vea aunque no estuviera conectado.

Destinatarios:
- Administradores del hotel (rol admin): todos los eventos.
- El cliente dueño de la reserva: los eventos que traen `usuarioId`
  (por ejemplo RESERVA_CREADA, sección 4.2).

Elementos que escribe en HotelSystemTable:
  Evento procesado  PK = EVENTO#<eventId>          SK = PROCESADO            (ttl 1 día)
  Alerta            PK = HOTEL#<hotelId>           SK = ALERTA#<ocurridoEn>#<eventId>
"""
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional en AWS
    pass

TABLE_NAME = os.getenv("TABLE_NAME", "HotelSystemTable")
HOTEL_ID = os.getenv("HOTEL_ID", "aloft-mvd")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT")
WS_ENDPOINT = os.getenv("WS_ENDPOINT")  # https://<api>.execute-api.<region>.amazonaws.com/<stage>

PROCESSED_TTL_SECONDS = 24 * 60 * 60

# Eventos que solo informan al cliente y no son alertas del panel.
EVENTOS_SIN_ALERTA = {"RESERVA_CREADA"}

dynamodb_config = {"region_name": AWS_REGION}
if DYNAMODB_ENDPOINT:
    dynamodb_config.update({
        "endpoint_url": DYNAMODB_ENDPOINT,
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "local"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
    })

table = boto3.resource("dynamodb", **dynamodb_config).Table(TABLE_NAME)

_ws_client = None


def ws_client():
    global _ws_client
    if _ws_client is None:
        if not WS_ENDPOINT:
            raise RuntimeError("WS_ENDPOINT sin definir.")
        _ws_client = boto3.client(
            "apigatewaymanagementapi", endpoint_url=WS_ENDPOINT, region_name=AWS_REGION
        )
    return _ws_client


# -----------------------------
# Idempotencia por eventId
# -----------------------------

def mark_processed(event_id):
    """True si el evento es nuevo; False si ya se había procesado."""
    try:
        table.put_item(
            Item={
                "PK": f"EVENTO#{event_id}",
                "SK": "PROCESADO",
                "ttl": int(time.time()) + PROCESSED_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def unmark_processed(event_id):
    """Si el envío falló, se libera la marca para que el reintento lo procese."""
    table.delete_item(Key={"PK": f"EVENTO#{event_id}", "SK": "PROCESADO"})


# -----------------------------
# Conexiones y alertas
# -----------------------------

def hotel_connections(hotel_id):
    items, start_key = [], None
    while True:
        params = {"KeyConditionExpression": Key("PK").eq(f"HOTEL#{hotel_id}#CONN")}
        if start_key:
            params["ExclusiveStartKey"] = start_key
        response = table.query(**params)
        items.extend(response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def recipients(event, connections):
    """Admins del hotel y, si el evento lo indica, el cliente dueño (sección 6.6)."""
    user_id = event.get("usuarioId")
    return [
        c for c in connections
        if c.get("rol") == "admin" or (user_id and c.get("usuario_id") == user_id)
    ]


def save_alert(event):
    hotel_id = event.get("hotelId") or HOTEL_ID
    occurred_at = event.get("ocurridoEn") or datetime.now(timezone.utc).isoformat()
    table.put_item(Item=json.loads(json.dumps({
        **event,
        "PK": f"HOTEL#{hotel_id}",
        "SK": f"ALERTA#{occurred_at}#{event['eventId']}",
        "leida": False,
    }), parse_float=Decimal))


def send(connection, data):
    """Envía el mensaje; si la conexión ya no existe (410), la borra."""
    try:
        ws_client().post_to_connection(ConnectionId=connection["connection_id"], Data=data)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "GoneException":
            table.delete_item(Key={"PK": connection["PK"], "SK": connection["SK"]})
            return False
        raise


# -----------------------------
# Procesamiento
# -----------------------------

def process_event(event):
    event_id = event.get("eventId")
    if not event_id or not event.get("tipo"):
        # Mensaje inválido: no tiene sentido reintentarlo.
        print(f"[avisos] Evento descartado por formato inválido: {sorted(event)}")
        return

    if not mark_processed(event_id):
        print(f"[avisos] Evento repetido descartado: {event_id}")
        return

    try:
        if event["tipo"] not in EVENTOS_SIN_ALERTA:
            save_alert(event)

        hotel_id = event.get("hotelId") or HOTEL_ID
        data = json.dumps(event, ensure_ascii=False).encode("utf-8")
        sent = sum(send(c, data) for c in recipients(event, hotel_connections(hotel_id)))
        print(f"[avisos] {event['tipo']} {event_id}: enviado a {sent} conexión(es).")
    except Exception:
        unmark_processed(event_id)
        raise


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
        except (TypeError, ValueError):
            print(f"[avisos] Mensaje no JSON descartado: {record.get('messageId')}")
            continue

        try:
            process_event(body)
        except Exception as error:  # noqa: BLE001
            print(f"[avisos] Error procesando {record.get('messageId')}: {error!r}")
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
