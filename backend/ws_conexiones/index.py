"""
Rutas de la API WebSocket (secciones 3 y 6.6).

- $connect: guarda el connectionId en DynamoDB con el usuario, el rol y el
  hotel que dejó el authorizer.
- $disconnect: borra el connectionId.
- $default: responde al ping periódico del cliente (la conexión se corta a
  los 10 minutos de inactividad).

Elemento de conexión en HotelSystemTable:
  PK = HOTEL#<hotelId>#CONN   SK = CONN#<connectionId>
  usuario_id, rol, hotel_id, conectado_en, ttl
API Gateway corta las conexiones a las 2 horas; el TTL limpia las que no
llegaron a pasar por $disconnect.
"""
import json
import os
import time
from datetime import datetime, timezone

import boto3

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional en AWS
    pass

TABLE_NAME = os.getenv("TABLE_NAME", "HotelSystemTable")
HOTEL_ID = os.getenv("HOTEL_ID", "aloft-mvd")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT")
CONNECTION_TTL_SECONDS = 3 * 60 * 60

dynamodb_config = {"region_name": AWS_REGION}
if DYNAMODB_ENDPOINT:
    dynamodb_config.update({
        "endpoint_url": DYNAMODB_ENDPOINT,
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "local"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
    })

table = boto3.resource("dynamodb", **dynamodb_config).Table(TABLE_NAME)


def connection_key(hotel_id, connection_id):
    return {"PK": f"HOTEL#{hotel_id}#CONN", "SK": f"CONN#{connection_id}"}


def on_connect(request_context):
    auth = request_context.get("authorizer") or {}
    hotel_id = auth.get("hotel_id") or HOTEL_ID
    connection_id = request_context["connectionId"]

    table.put_item(Item={
        **connection_key(hotel_id, connection_id),
        "connection_id": connection_id,
        "usuario_id": auth.get("usuario_id", ""),
        "rol": auth.get("rol", "cliente"),
        "hotel_id": hotel_id,
        "conectado_en": datetime.now(timezone.utc).isoformat(),
        "ttl": int(time.time()) + CONNECTION_TTL_SECONDS,
    })
    return {"statusCode": 200}


def on_disconnect(request_context):
    auth = request_context.get("authorizer") or {}
    hotel_id = auth.get("hotel_id") or HOTEL_ID
    table.delete_item(Key=connection_key(hotel_id, request_context["connectionId"]))
    return {"statusCode": 200}


def on_default(event):
    # Cualquier mensaje del cliente (ping) mantiene viva la conexión.
    return {"statusCode": 200, "body": json.dumps({"tipo": "PONG"})}


def lambda_handler(event, context):
    request_context = event.get("requestContext") or {}
    route = request_context.get("routeKey")

    try:
        if route == "$connect":
            return on_connect(request_context)
        if route == "$disconnect":
            return on_disconnect(request_context)
        return on_default(event)
    except Exception as error:  # noqa: BLE001
        print(f"Error en Lambda ws_conexiones ({route}): {error!r}")
        return {"statusCode": 500}
