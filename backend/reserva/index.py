import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional en AWS
    pass


# -----------------------------
# Configuración
# -----------------------------

TABLE_NAME = os.getenv("TABLE_NAME", "HotelSystemTable")
HOTEL_ID = os.getenv("HOTEL_ID", "aloft-mvd")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
HOTEL_TIMEZONE = os.getenv("HOTEL_TIMEZONE", "America/Montevideo")
EVENTS_QUEUE_URL = os.getenv("EVENTS_QUEUE_URL")  # vacío = no publicar eventos
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT")
SQS_ENDPOINT = os.getenv("SQS_ENDPOINT")

# Una transacción admite 100 operaciones: una por (tipo, noche) + la reserva.
MAX_TRANSACTION_ITEMS = 100
MAX_NIGHTS = int(os.getenv("MAX_NIGHTS", "30"))
MAX_ROOMS_PER_BOOKING = int(os.getenv("MAX_ROOMS_PER_BOOKING", "10"))


def _client_config(endpoint):
    config = {"region_name": AWS_REGION}
    if endpoint:
        # Emuladores locales: no validan las credenciales.
        config.update({
            "endpoint_url": endpoint,
            "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "local"),
            "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
        })
    return config


dynamodb = boto3.resource("dynamodb", **_client_config(DYNAMODB_ENDPOINT))
table = dynamodb.Table(TABLE_NAME)
dynamodb_client = dynamodb.meta.client
sqs = boto3.client("sqs", **_client_config(SQS_ENDPOINT))


class BookingError(Exception):
    """Error de negocio que se devuelve al cliente con su código HTTP."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# -----------------------------
# Identidad y validación de la solicitud
# -----------------------------

def get_user_id(event):
    """
    El usuario sale SIEMPRE del JWT validado por API Gateway (claim `sub`),
    nunca del cuerpo de la solicitud.
    """
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = authorizer.get("claims") or authorizer.get("jwt", {}).get("claims") or {}
    user_id = claims.get("sub")

    if not user_id:
        raise BookingError(401, "Debes iniciar sesión para reservar.")

    return user_id


def get_idempotency_key(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get("idempotency-key")


def parse_body(event):
    raw = event.get("body")
    if raw is None or raw == "":
        raise BookingError(400, "El cuerpo de la solicitud es obligatorio.")

    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        raise BookingError(400, "El cuerpo debe ser un JSON válido.")

    if not isinstance(body, dict):
        raise BookingError(400, "El cuerpo debe ser un objeto JSON.")

    return body


def parse_dates(body):
    try:
        check_in = date.fromisoformat(body["entrada"])
        check_out = date.fromisoformat(body["salida"])
    except (KeyError, TypeError, ValueError):
        raise BookingError(
            400, "entrada y salida son obligatorias con formato YYYY-MM-DD."
        )

    if check_out <= check_in:
        raise BookingError(400, "La salida debe ser posterior a la entrada.")

    # Las noches son fechas locales del hotel.
    hotel_today = datetime.now(ZoneInfo(HOTEL_TIMEZONE)).date()
    if check_in < hotel_today:
        raise BookingError(400, "La entrada no puede ser una fecha pasada.")

    nights = (check_out - check_in).days
    if nights > MAX_NIGHTS:
        raise BookingError(400, f"La estadía máxima es de {MAX_NIGHTS} noches.")

    return check_in, check_out, nights


def parse_rooms(body):
    """
    Lee las habitaciones pedidas: [{"tipo_id": "KING-RIVER", "cantidad": 1}].
    Es el mismo formato que devuelve la Lambda Consulta en `habitaciones`.
    Se acumulan duplicados para no generar dos operaciones sobre el mismo ítem
    (DynamoDB no permite dos operaciones sobre el mismo ítem en una transacción).
    """
    rooms = body.get("habitaciones")

    if not isinstance(rooms, list) or not rooms:
        raise BookingError(400, "habitaciones debe ser una lista con al menos un tipo.")

    quantities = {}
    for room in rooms:
        if not isinstance(room, dict):
            raise BookingError(400, "Cada habitación debe ser un objeto.")

        type_id = room.get("tipo_id")
        quantity = room.get("cantidad")

        if not isinstance(type_id, str) or not type_id:
            raise BookingError(400, "Cada habitación necesita tipo_id.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise BookingError(400, "cantidad debe ser un entero mayor o igual a 1.")

        quantities[type_id] = quantities.get(type_id, 0) + quantity

    if sum(quantities.values()) > MAX_ROOMS_PER_BOOKING:
        raise BookingError(
            400, f"Una reserva admite hasta {MAX_ROOMS_PER_BOOKING} habitaciones."
        )

    return quantities


def parse_guests(body):
    guests = body.get("huespedes")
    if guests is None:
        return None
    if isinstance(guests, bool) or not isinstance(guests, int) or guests < 1:
        raise BookingError(400, "huespedes debe ser un entero mayor o igual a 1.")
    return guests


# -----------------------------
# Catálogo y precios (siempre del servidor)
# -----------------------------

def load_room_types(type_ids):
    """Lee los tipos pedidos. El precio nunca se toma del cliente."""
    hotel_pk = f"HOTEL#{HOTEL_ID}"
    keys = [{"PK": hotel_pk, "SK": f"TIPO#{type_id}"} for type_id in type_ids]

    response = dynamodb.batch_get_item(
        RequestItems={TABLE_NAME: {"Keys": keys, "ConsistentRead": True}}
    )
    found = {
        item["SK"].removeprefix("TIPO#"): item
        for item in response["Responses"].get(TABLE_NAME, [])
    }

    missing = [type_id for type_id in type_ids if type_id not in found]
    if missing:
        raise BookingError(
            400, f"Tipos de habitación inexistentes: {', '.join(sorted(missing))}."
        )

    return found


# -----------------------------
# Transacción de reserva
# -----------------------------

def stay_nights(check_in, nights):
    return [(check_in + timedelta(days=i)).isoformat() for i in range(nights)]


def build_transaction(reservation, quantities, night_list):
    """
    Un Update condicional por (tipo, noche) + el Put de la reserva.

    La condición `disponibles >= :qty` se evalúa dentro de DynamoDB, de forma
    atómica con el descuento: dos reservas simultáneas no pueden pasar ambas
    cuando solo queda una unidad. Si alguna condición falla, falla toda la
    transacción y no se descuenta nada.
    """
    items = []

    for type_id, quantity in quantities.items():
        inventory_pk = f"HOTEL#{HOTEL_ID}#TIPO#{type_id}"
        for night in night_list:
            items.append({
                "Update": {
                    "TableName": TABLE_NAME,
                    "Key": {"PK": inventory_pk, "SK": f"NOCHE#{night}"},
                    "UpdateExpression": "SET disponibles = disponibles - :qty",
                    "ConditionExpression": (
                        "attribute_exists(PK) AND disponibles >= :qty"
                    ),
                    "ExpressionAttributeValues": {":qty": quantity},
                }
            })

    items.append({
        "Put": {
            "TableName": TABLE_NAME,
            "Item": reservation,
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    })

    if len(items) > MAX_TRANSACTION_ITEMS:
        raise BookingError(
            400,
            "La reserva combina demasiadas noches y tipos de habitación; "
            "dividila en reservas más pequeñas.",
        )

    return items


def run_transaction(items, token):
    try:
        dynamodb_client.transact_write_items(
            TransactItems=items,
            ClientRequestToken=token,
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")

        if code == "TransactionCanceledException":
            reasons = error.response.get("CancellationReasons") or []
            codes = {reason.get("Code") for reason in reasons}
            if "ConditionalCheckFailed" in codes:
                raise BookingError(
                    409,
                    "Ya no hay disponibilidad para esas fechas. "
                    "Elegí otra opción.",
                )
            if "TransactionConflict" in codes:
                raise BookingError(
                    409, "Hay otra reserva en curso sobre esas noches. Reintentá."
                )

        raise


# -----------------------------
# Eventos hacia SQS
# -----------------------------

def publish_event(event_type, payload):
    """
    Publica un evento. Si SQS falla, la reserva ya está confirmada: se registra
    el error y no se revierte (la notificación es secundaria).
    """
    if not EVENTS_QUEUE_URL:
        print(f"[eventos] {event_type} no publicado: EVENTS_QUEUE_URL sin definir.")
        return

    message = {
        "eventId": str(uuid.uuid4()),
        "tipo": event_type,
        "hotelId": HOTEL_ID,
        "ocurridoEn": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

    try:
        sqs.send_message(
            QueueUrl=EVENTS_QUEUE_URL,
            MessageBody=json.dumps(message, default=_json_default, ensure_ascii=False),
        )
    except Exception as error:  # noqa: BLE001
        print(f"[eventos] Error publicando {event_type}: {error}")


def find_sold_out_types(quantities, night_list):
    """Tipos que quedaron en 0 en alguna noche tras la reserva."""
    sold_out = {}

    for type_id in quantities:
        inventory_pk = f"HOTEL#{HOTEL_ID}#TIPO#{type_id}"
        keys = [{"PK": inventory_pk, "SK": f"NOCHE#{n}"} for n in night_list]
        response = dynamodb.batch_get_item(
            RequestItems={TABLE_NAME: {"Keys": keys, "ConsistentRead": True}}
        )
        nights_at_zero = sorted(
            item["fecha"]
            for item in response["Responses"].get(TABLE_NAME, [])
            if int(item["disponibles"]) <= 0
        )
        if nights_at_zero:
            sold_out[type_id] = nights_at_zero

    return sold_out


# -----------------------------
# Respuestas
# -----------------------------

def _json_default(value):
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
        "body": json.dumps(payload, default=_json_default, ensure_ascii=False),
    }


# -----------------------------
# Handler de AWS Lambda
# -----------------------------

def lambda_handler(event, context):
    """POST /reservas — requiere JWT de Cognito (validado por API Gateway)."""
    try:
        user_id = get_user_id(event)
        body = parse_body(event)
        check_in, check_out, nights = parse_dates(body)
        quantities = parse_rooms(body)
        guests = parse_guests(body)

        room_types = load_room_types(list(quantities))

        capacity = sum(
            int(room_types[type_id]["capacidad"]) * quantity
            for type_id, quantity in quantities.items()
        )
        if guests is not None and guests > capacity:
            raise BookingError(
                400, "Las habitaciones elegidas no alcanzan para todos los huéspedes."
            )

        total = sum(
            Decimal(str(room_types[type_id]["precio_base"])) * quantity * nights
            for type_id, quantity in quantities.items()
        )

        # Con Idempotency-Key, un reintento del mismo cliente reutiliza el
        # mismo id y el mismo token: DynamoDB no repite la operación.
        idempotency_key = get_idempotency_key(event)
        if idempotency_key:
            token = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user_id}:{idempotency_key}"))
        else:
            token = str(uuid.uuid4())

        reservation_id = f"RESV-{token}"

        if idempotency_key:
            existing = table.get_item(
                Key={"PK": f"HOTEL#{HOTEL_ID}", "SK": f"RES#{reservation_id}"},
                ConsistentRead=True,
            ).get("Item")
            if existing:
                # Reintento de una reserva ya creada: se devuelve la misma.
                return make_response(200, {"reserva": existing})

        night_list = stay_nights(check_in, nights)

        reservation = {
            "PK": f"HOTEL#{HOTEL_ID}",
            "SK": f"RES#{reservation_id}",
            "reserva_id": reservation_id,
            "usuario_id": user_id,
            "habitaciones": [
                {"tipo_id": type_id, "cantidad": quantity}
                for type_id, quantity in quantities.items()
            ],
            "fecha_inicio": check_in.isoformat(),
            "fecha_fin": check_out.isoformat(),
            "noches": nights,
            "estado": "CONFIRMADA",
            "precio_total": total,
            "creada_en": datetime.now(timezone.utc).isoformat(),
        }
        if guests is not None:
            reservation["huespedes"] = guests

        transaction = build_transaction(reservation, quantities, night_list)
        run_transaction(transaction, token)

        # Solo se avisa al admin cuando un tipo se agota; cada reserva
        # individual no genera evento (el panel la consulta cuando lo abre).
        for type_id, nights_at_zero in find_sold_out_types(
            quantities, night_list
        ).items():
            publish_event("CAPACIDAD_AGOTADA", {
                "tipoHabitacion": type_id,
                "noches": nights_at_zero,
            })

        return make_response(201, {"reserva": reservation})

    except BookingError as error:
        return make_response(error.status_code, {"error": error.message})

    except Exception as error:  # noqa: BLE001
        # El detalle queda en CloudWatch; el cliente no ve datos internos.
        print(f"Error en Lambda Reserva: {error!r}")
        return make_response(500, {"error": "Ocurrió un error al crear la reserva."})
