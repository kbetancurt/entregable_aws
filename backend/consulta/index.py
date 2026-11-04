import json
import os
from datetime import date, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

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
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT")

# Si se define DYNAMODB_ENDPOINT, el script se conecta a DynamoDB Local.
# Si no se define, boto3 utiliza la configuración de AWS.
dynamodb_config = {
    "region_name": AWS_REGION,
}

if DYNAMODB_ENDPOINT:
    dynamodb_config.update({
        "endpoint_url": DYNAMODB_ENDPOINT,
        # DynamoDB Local no valida estas credenciales.
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "local"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
    })

dynamodb = boto3.resource("dynamodb", **dynamodb_config)
table = dynamodb.Table(TABLE_NAME)


# -----------------------------
# Lectura y validación de la solicitud
# -----------------------------

def read_query_parameters(event):
    """Lee y valida los parámetros que llegan por query string."""
    params = event.get("queryStringParameters") or {}

    check_in_text = params.get("entrada")
    check_out_text = params.get("salida")
    guests_text = params.get("huespedes")
    requested_type = params.get("tipo")

    if not check_in_text or not check_out_text or not guests_text:
        raise ValueError(
            "Los parámetros entrada, salida y huespedes son obligatorios."
        )

    try:
        check_in = date.fromisoformat(check_in_text)
        check_out = date.fromisoformat(check_out_text)
        guests = int(guests_text)
    except ValueError:
        raise ValueError(
            "Usa fechas con formato YYYY-MM-DD y huéspedes como número entero."
        )

    if check_out <= check_in:
        raise ValueError("La salida debe ser posterior a la entrada.")

    if guests < 1:
        raise ValueError("Debe haber al menos un huésped.")

    return check_in, check_out, guests, requested_type


# -----------------------------
# Consulta de DynamoDB
# -----------------------------

def query_all(key_condition):
    """Consulta una partición y recoge todos los resultados paginados."""
    items = []
    query_params = {
        "KeyConditionExpression": key_condition,
    }

    while True:
        response = table.query(**query_params)
        items.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

        query_params["ExclusiveStartKey"] = last_key

    return items


def get_available_room_types(check_in, check_out, requested_type=None):
    """
    Devuelve tipos con inventario disponible durante todas las noches.

    unidades_disponibles_estadia es el número máximo de unidades de ese tipo
    que se pueden ofrecer para toda la estadía. Se calcula tomando el menor
    inventario de todas las noches.
    """
    hotel_pk = f"HOTEL#{HOTEL_ID}"

    # El catálogo guarda los tipos bajo:
    # PK = HOTEL#aloft-mvd
    # SK = TIPO#<tipoId>
    room_types = query_all(
        Key("PK").eq(hotel_pk) & Key("SK").begins_with("TIPO#")
    )

    # Si se pidió un tipo concreto, verificar que exista en el catálogo.
    if requested_type:
        type_exists = any(
            room_type.get("id") == requested_type
            for room_type in room_types
        )
        if not type_exists:
            raise ValueError(f"El tipo de habitación '{requested_type}' no existe.")

    # El día de salida no es una noche reservada.
    first_night = check_in
    last_night = check_out - timedelta(days=1)

    available_types = []

    for room_type in room_types:
        room_type_id = room_type["id"]

        # Si se pidió un tipo, ignorar los demás.
        if requested_type and room_type_id != requested_type:
            continue

        # El inventario guarda una fila por tipo y noche:
        # PK = HOTEL#aloft-mvd#TIPO#KING-ALOFT
        # SK = NOCHE#2026-10-27
        inventory_pk = f"{hotel_pk}#TIPO#{room_type_id}"

        inventory_items = query_all(
            Key("PK").eq(inventory_pk)
            & Key("SK").between(
                f"NOCHE#{first_night.isoformat()}",
                f"NOCHE#{last_night.isoformat()}",
            )
        )

        # Convierte los registros de inventario en:
        # {"2026-10-27": 5, "2026-10-28": 3, ...}
        available_by_night = {
            item["fecha"]: int(item["disponibles"])
            for item in inventory_items
        }

        # Lee la disponibilidad de cada noche de la estadía.
        # Si falta una noche, se considera que no hay disponibilidad.
        nightly_counts = []
        night = check_in

        while night < check_out:
            nightly_counts.append(
                available_by_night.get(night.isoformat(), 0)
            )
            night += timedelta(days=1)

        # Solo podemos ofrecer tantas habitaciones como haya en la noche
        # con menos inventario.
        units_for_entire_stay = min(nightly_counts, default=0)

        if units_for_entire_stay > 0:
            available_type = dict(room_type)
            available_type["unidades_disponibles_estadia"] = (
                units_for_entire_stay
            )
            available_types.append(available_type)

    return available_types


# -----------------------------
# Combinación de habitaciones
# -----------------------------

def build_combinations(room_types, guests, number_of_nights):
    """
    Arma opciones con capacidad suficiente para alojar al grupo.

    Puede combinar más de un tipo de habitación. La cantidad de cada tipo
    no puede superar su disponibilidad durante todas las noches.
    """
    # Primero intenta habitaciones con más capacidad y luego menor precio.
    room_types = sorted(
        room_types,
        key=lambda room_type: (
            -int(room_type["capacidad"]),
            Decimal(str(room_type["precio_base"])),
        ),
    )

    combinations = []

    def search(
        index,
        selected_rooms,
        total_capacity,
        total_room_count,
        total_price,
    ):
        # Si ya alcanza la capacidad, guardamos la combinación.
        if total_capacity >= guests:
            combinations.append({
                "habitaciones": list(selected_rooms),
                "cantidad_habitaciones": total_room_count,
                "capacidad_total": total_capacity,
                "precio_total": total_price,
                "noches": number_of_nights,
            })
            return

        # Ya se revisaron todos los tipos sin alcanzar la capacidad necesaria.
        if index >= len(room_types):
            return

        room_type = room_types[index]
        room_type_capacity = int(room_type["capacidad"])
        available_units = int(
            room_type["unidades_disponibles_estadia"]
        )
        price_per_night = Decimal(str(room_type["precio_base"]))

        # Probamos cero o más habitaciones de este tipo,
        # hasta su disponibilidad para toda la estadía.
        for quantity in range(available_units + 1):
            added_capacity = quantity * room_type_capacity
            added_price = (
                price_per_night * quantity * number_of_nights
            )

            if quantity > 0:
                selected_rooms.append({
                    "tipo_id": room_type["id"],
                    "nombre": room_type["nombre"],
                    "cantidad": quantity,
                    "capacidad_por_habitacion": room_type_capacity,
                    "precio_por_noche": price_per_night,
                })

            search(
                index + 1,
                selected_rooms,
                total_capacity + added_capacity,
                total_room_count + quantity,
                total_price + added_price,
            )

            if quantity > 0:
                selected_rooms.pop()

            # Si esta cantidad ya alcanza, no probamos más habitaciones
            # de este mismo tipo: serían habitaciones innecesarias.
            if total_capacity + added_capacity >= guests:
                break

    search(
        index=0,
        selected_rooms=[],
        total_capacity=0,
        total_room_count=0,
        total_price=Decimal("0"),
    )

    # Prioriza menos habitaciones; después, menor precio y menor capacidad
    # sobrante.
    combinations.sort(
        key=lambda option: (
            option["cantidad_habitaciones"],
            option["precio_total"],
            option["capacidad_total"] - guests,
        )
    )

    return combinations


# -----------------------------
# Conversión de valores para JSON
# -----------------------------

def json_default(value):
    """Convierte valores Decimal de DynamoDB a números JSON."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    raise TypeError(f"Tipo no serializable: {type(value).__name__}")


def make_response(status_code, payload):
    """Crea una respuesta compatible con API Gateway REST."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(
            payload,
            default=json_default,
            ensure_ascii=False,
        ),
    }


# -----------------------------
# Handler de AWS Lambda
# -----------------------------

def lambda_handler(event, context):
    """Punto de entrada de la Lambda."""
    try:
        check_in, check_out, guests, requested_type = (
            read_query_parameters(event)
        )

        room_types = get_available_room_types(
            check_in=check_in,
            check_out=check_out,
            requested_type=requested_type,
        )

        number_of_nights = (check_out - check_in).days

        combinations = build_combinations(
            room_types=room_types,
            guests=guests,
            number_of_nights=number_of_nights,
        )

        return make_response(200, {
            "hotel_id": HOTEL_ID,
            "entrada": check_in.isoformat(),
            "salida": check_out.isoformat(),
            "noches": number_of_nights,
            "huespedes": guests,
            "combinaciones": combinations,
        })

    except ValueError as error:
        return make_response(400, {
            "error": str(error),
        })

    except Exception as error:
        # El detalle queda en CloudWatch para depuración.
        # La respuesta al cliente no expone detalles internos.
        print(f"Error en Lambda Consulta: {error}")

        return make_response(500, {
            "error": "Ocurrió un error al consultar disponibilidad.",
        })