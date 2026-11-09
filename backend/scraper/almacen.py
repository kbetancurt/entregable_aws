"""
Acceso a AWS del scraper: DynamoDB (catálogo, tarifa propia y último estado observado),
SQS (eventos) y S3 (respaldo crudo opcional). Separado del handler para poder probarlo.

Elementos que escribe en HotelSystemTable (PK = HOTEL#<hotelId>#SCRAPER):
  Último precio   SK = PRECIO#<plataforma>#<tipoId|HOTEL>#<noche>   precio, disponible, menor, observado_en
  Amenities       SK = AMENITIES#<plataforma>                      conjunto, hash, observado_en
"""
import json
import os
import uuid
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
SQS_ENDPOINT = os.getenv("SQS_ENDPOINT")
EVENTS_QUEUE_URL = os.getenv("EVENTS_QUEUE_URL")  # vacío = no publicar eventos
SCRAPER_BUCKET = os.getenv("SCRAPER_BUCKET")      # vacío = no guardar respaldo crudo


def _config(endpoint):
    config = {"region_name": AWS_REGION}
    if endpoint:
        config.update({
            "endpoint_url": endpoint,
            "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "local"),
            "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
        })
    return config


table = boto3.resource("dynamodb", **_config(DYNAMODB_ENDPOINT)).Table(TABLE_NAME)
sqs = boto3.client("sqs", **_config(SQS_ENDPOINT))
s3 = boto3.client("s3", region_name=AWS_REGION)

_PK = f"HOTEL#{HOTEL_ID}"
_PK_SCRAPER = f"HOTEL#{HOTEL_ID}#SCRAPER"


def _json_default(valor):
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    raise TypeError(f"Tipo no serializable: {type(valor).__name__}")


# -----------------------------
# Catálogo y tarifa propia
# -----------------------------

def leer_tipos():
    respuesta = table.query(
        KeyConditionExpression=Key("PK").eq(_PK) & Key("SK").begins_with("TIPO#")
    )
    return respuesta.get("Items", [])


def leer_amenities_del_hotel():
    item = table.get_item(Key={"PK": _PK, "SK": "HOTEL#METADATA"}).get("Item") or {}
    return item.get("amenities", [])


def tarifa_propia(tipo, noche):
    """Tarifa de la web del hotel para esa noche: ítem TARIFA# si existe, si no el precio base."""
    item = table.get_item(Key={"PK": _PK, "SK": f"TARIFA#{tipo['id']}#{noche}"}).get("Item")
    if item:
        return Decimal(str(item["precio"]))
    return Decimal(str(tipo["precio_base"]))


# -----------------------------
# Último estado observado
# -----------------------------

def _sk_precio(plataforma, tipo_id, noche):
    return f"PRECIO#{plataforma}#{tipo_id or 'HOTEL'}#{noche}"


def leer_precio_previo(plataforma, tipo_id, noche):
    return table.get_item(
        Key={"PK": _PK_SCRAPER, "SK": _sk_precio(plataforma, tipo_id, noche)}
    ).get("Item")


def guardar_precio(plataforma, tipo_id, noche, estado, observado_en):
    item = {
        "PK": _PK_SCRAPER,
        "SK": _sk_precio(plataforma, tipo_id, noche),
        "plataforma": plataforma,
        "tipo_id": tipo_id or "HOTEL",
        "noche": noche,                 # fecha de la estadía
        "observado_en": observado_en,   # cuándo corrió el scraper
        "disponible": estado["disponible"],
        "menor": estado["menor"],
    }
    if estado["precio"] is not None:    # sin tarifas: se omite, nunca se guarda 0
        item["precio"] = estado["precio"]
    table.put_item(Item=item)


def leer_amenities_previas(plataforma):
    return table.get_item(Key={"PK": _PK_SCRAPER, "SK": f"AMENITIES#{plataforma}"}).get("Item")


def guardar_amenities(plataforma, estado, observado_en):
    table.put_item(Item={
        "PK": _PK_SCRAPER,
        "SK": f"AMENITIES#{plataforma}",
        "conjunto": estado["conjunto"],
        "hash": estado["hash"],
        "faltantes": estado.get("faltantes", []),
        "observado_en": observado_en,
    })


# -----------------------------
# Eventos y respaldo
# -----------------------------

def publicar_evento(evento):
    """Publica en SQS. Si falla, se registra y se sigue: una alerta perdida no frena el scraper."""
    mensaje = {
        "eventId": str(uuid.uuid4()),
        "hotelId": HOTEL_ID,
        "ocurridoEn": datetime.now(timezone.utc).isoformat(),
        **evento,
    }
    if not EVENTS_QUEUE_URL:
        print(f"[scraper] {evento['tipo']} no publicado: EVENTS_QUEUE_URL sin definir.")
        return False
    try:
        sqs.send_message(
            QueueUrl=EVENTS_QUEUE_URL,
            MessageBody=json.dumps(mensaje, default=_json_default, ensure_ascii=False),
        )
        return True
    except ClientError as error:
        print(f"[scraper] Error publicando {evento['tipo']}: {error}")
        return False


def guardar_respaldo_crudo(plataforma, noche, observado_en, datos):
    """Respuesta cruda del modelo en S3, para auditar y reprocesar. Opcional."""
    if not SCRAPER_BUCKET:
        return
    clave = f"raw/{observado_en[:10]}/{plataforma}/{noche}.json"
    try:
        s3.put_object(
            Bucket=SCRAPER_BUCKET, Key=clave, ContentType="application/json",
            Body=json.dumps(datos, default=_json_default, ensure_ascii=False).encode("utf-8"),
        )
    except ClientError as error:
        print(f"[scraper] No se pudo guardar el respaldo {clave}: {error}")
