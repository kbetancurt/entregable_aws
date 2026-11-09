"""
Lambda Alertas (campanita del panel de administración).

  GET  /alertas?limite=50   alertas guardadas por la Lambda Avisos, las más nuevas primero
  POST /alertas/leer        marca como leídas: {"ids": ["ALERTA#..."]} o {"todas": true}

Solo para administradores (grupo "admin" de Cognito). Las alertas las escribe Avisos con
PK = HOTEL#<hotelId> y SK = ALERTA#<ocurridoEn>#<eventId>; la SK sirve de id.
"""
import json
import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
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

dynamodb_config = {"region_name": AWS_REGION}
if DYNAMODB_ENDPOINT:
    dynamodb_config.update({
        "endpoint_url": DYNAMODB_ENDPOINT,
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "local"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
    })

table = boto3.resource("dynamodb", **dynamodb_config).Table(TABLE_NAME)

PK = f"HOTEL#{HOTEL_ID}"
LIMITE_POR_DEFECTO = 50
LIMITE_MAXIMO = 100
MAX_IDS = 100


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Tipo no serializable: {type(value).__name__}")


def make_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(payload, default=json_default, ensure_ascii=False),
    }


def claims_of(event):
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    return authorizer.get("claims") or authorizer.get("jwt", {}).get("claims") or {}


def is_admin(event):
    """API Gateway entrega cognito:groups como texto ("admin" o "[admin cliente]")."""
    raw = claims_of(event).get("cognito:groups") or ""
    groups = set(raw) if isinstance(raw, list) else set(re.split(r"[\s,\[\]]+", str(raw)))
    return "admin" in groups


def parse_body(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        raise ValueError("El cuerpo debe ser JSON.")
    if not isinstance(body, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON.")
    return body


def public_alert(item):
    """Lo que ve el front: sin claves internas y con el id de la alerta."""
    alert = {k: v for k, v in item.items() if k not in ("PK", "SK")}
    alert["id"] = item["SK"]
    alert["leida"] = bool(item.get("leida"))
    return alert


# -----------------------------
# GET /alertas
# -----------------------------

def list_alerts(event):
    params = event.get("queryStringParameters") or {}
    try:
        limit = int(params.get("limite", LIMITE_POR_DEFECTO))
    except ValueError:
        raise ValueError("limite debe ser un número entero.")
    if not 1 <= limit <= LIMITE_MAXIMO:
        raise ValueError(f"limite debe estar entre 1 y {LIMITE_MAXIMO}.")

    response = table.query(
        KeyConditionExpression=Key("PK").eq(PK) & Key("SK").begins_with("ALERTA#"),
        ScanIndexForward=False,  # las más nuevas primero (la SK empieza con la fecha)
        Limit=limit,
    )
    alerts = [public_alert(item) for item in response.get("Items", [])]
    return make_response(200, {
        "alertas": alerts,
        "no_leidas": sum(1 for a in alerts if not a["leida"]),
    })


# -----------------------------
# POST /alertas/leer
# -----------------------------

def mark_read(event):
    body = parse_body(event)

    if body.get("todas") is True:
        ids, kwargs = [], {
            "KeyConditionExpression": Key("PK").eq(PK) & Key("SK").begins_with("ALERTA#"),
            "FilterExpression": Attr("leida").ne(True),
            "ProjectionExpression": "SK",
        }
        while True:
            response = table.query(**kwargs)
            ids += [item["SK"] for item in response.get("Items", [])]
            if "LastEvaluatedKey" not in response or len(ids) >= 500:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    else:
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError('Indicá "ids" (lista) o "todas": true.')
        if len(ids) > MAX_IDS:
            raise ValueError(f"Como máximo {MAX_IDS} alertas por pedido.")
        if not all(isinstance(i, str) and i.startswith("ALERTA#") for i in ids):
            raise ValueError("Cada id debe ser el de una alerta (empieza con ALERTA#).")

    updated = 0
    for alert_id in ids:
        try:
            table.update_item(
                Key={"PK": PK, "SK": alert_id},
                UpdateExpression="SET leida = :true",
                ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeValues={":true": True},
            )
            updated += 1
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise  # una alerta inexistente se ignora; cualquier otro error no
    return make_response(200, {"marcadas": updated})


# -----------------------------
# Handler
# -----------------------------

def lambda_handler(event, context):
    try:
        if not is_admin(event):
            return make_response(403, {"error": "Solo los administradores pueden ver las alertas."})

        method = (event.get("httpMethod") or "").upper()
        path = (event.get("path") or "").rstrip("/")

        if method == "GET" and path.endswith("/alertas"):
            return list_alerts(event)
        if method == "POST" and path.endswith("/alertas/leer"):
            return mark_read(event)
        return make_response(404, {"error": "Ruta no encontrada."})

    except ValueError as error:
        return make_response(400, {"error": str(error)})
    except Exception as error:  # noqa: BLE001
        print(f"Error en Lambda Alertas: {error!r}")
        return make_response(500, {"error": "Ocurrió un error al procesar las alertas."})
