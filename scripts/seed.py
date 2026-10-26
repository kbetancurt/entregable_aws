import os
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError


TABLE_NAME = os.environ.get("TABLE_NAME", "HotelSystemTable")
HOTEL_ID = os.environ.get("HOTEL_ID", "aloft-mvd")
DYNAMODB_ENDPOINT = os.environ.get(
    "DYNAMODB_ENDPOINT",
    "http://localhost:8000",
)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DAYS_TO_SEED = int(os.environ.get("DAYS_TO_SEED", "30"))

HOTEL = {
    "nombre": "Aloft Montevideo Hotel",
    "direccion": "Victor Soliño 350, Punta Carretas, Montevideo",
    "descripcion": (
        "Hotel moderno en Punta Carretas, cerca del centro comercial "
        "y de la Rambla."
    ),
    "amenities": [
        "Wi-Fi",
        "Piscina interior",
        "Gimnasio Re:Charge",
        "Restaurante Nook",
        "Bar W XYZ",
    ],
}

# Capacidades, precios e inventario para la demo.
# Ajustar cuando queden definidios los datos para presentar.
ROOM_TYPES = [
    {
        "id": "KING-ALOFT",
        "nombre": "Habitación King con vista a la ciudad",
        "camas": "1 cama King",
        "capacidad": 2,
        "precio_base": 145,
        "inventario_total": 5,
        "nombres_ota": {
            "Marriott": ["Aloft - Guest room, 1 King, City view"],
            "Booking.com": ["ALOFT, Guest room, 1 King, City view"],
        },
    },
    {
        "id": "TWIN-TWIN",
        "nombre": "Habitación Twin con vista a la ciudad",
        "camas": "2 camas Twin",
        "capacidad": 2,
        "precio_base": 145,
        "inventario_total": 3,
        "nombres_ota": {
            "Marriott": ["Aloft - Guest room, 2 Twin, City view"],
            "Booking.com": [
                "Aloft, Guest room, 2 Twin/Single Bed(s), City view"
            ],
        },
    },
    {
        "id": "QUEEN-QUEEN-CITY",
        "nombre": "Habitación con dos camas Queen y vista a la ciudad",
        "camas": "2 camas Queen",
        "capacidad": 4,
        "precio_base": 185,
        "inventario_total": 2,
        "nombres_ota": {
            "Marriott": ["Aloft - Guest room, 2 Queen, City view"],
            "Booking.com": ["ALOFT, Guest room, 2 Queen, City view"],
        },
    },
    {
        "id": "KING-RIVER",
        "nombre": "Habitación King con vista al río",
        "camas": "1 cama King",
        "capacidad": 2,
        "precio_base": 165,
        "inventario_total": 4,
        "nombres_ota": {
            "Marriott": ["River View - Guest room, 1 King"],
            "Booking.com": ["River View, Guest room, 1 King"],
        },
    },
    {
        "id": "QUEEN-QUEEN-RIVER",
        "nombre": "Habitación con dos camas Queen y vista al río",
        "camas": "2 camas Queen",
        "capacidad": 4,
        "precio_base": 185,
        "inventario_total": 1,
        "nombres_ota": {
            "Marriott": ["River View - Guest room, 2 Queen"],
            "Booking.com": ["River View, Guest room, 2 Queen"],
        },
    },
]

def create_dynamodb_resource():
    """Conecta solo con DynamoDB Local."""
    hostname = urlparse(DYNAMODB_ENDPOINT).hostname

    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(
            "Este seed solo puede apuntar a DynamoDB Local "
            "(localhost o 127.0.0.1)."
        )

    return boto3.resource(
        "dynamodb",
        endpoint_url=DYNAMODB_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


def create_table_if_missing(dynamodb):
    """Crea una tabla con PK y SK de tipo string si no existe."""
    client = dynamodb.meta.client

    try:
        client.describe_table(TableName=TABLE_NAME)
        print(f"La tabla {TABLE_NAME} ya existe.")
    except client.exceptions.ResourceNotFoundException:
        print(f"Creando la tabla {TABLE_NAME}...")

        dynamodb.create_table(
            TableName=TABLE_NAME,
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

        dynamodb.Table(TABLE_NAME).wait_until_exists()
        print(f"Tabla {TABLE_NAME} creada.")


def put_if_missing(table, item):
    """
    Inserta un elemento solo si todavía no existe.
    Si ya existe, lo conserva, incluyendo el inventario consumido.
    """
    try:
        table.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )
        return True
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return False
        raise


def get_start_date():
    configured_date = os.environ.get("SEED_START_DATE")

    if configured_date:
        try:
            return date.fromisoformat(configured_date)
        except ValueError as error:
            raise ValueError(
                "SEED_START_DATE debe tener el formato YYYY-MM-DD."
            ) from error

    hotel_today = datetime.now(
        ZoneInfo("America/Montevideo")
    ).date()

    # El inventario comienza mañana.
    return hotel_today + timedelta(days=1)


def main():
    if DAYS_TO_SEED < 1:
        raise ValueError("DAYS_TO_SEED debe ser al menos 1.")

    dynamodb = create_dynamodb_resource()
    create_table_if_missing(dynamodb)
    table = dynamodb.Table(TABLE_NAME)

    hotel_pk = f"HOTEL#{HOTEL_ID}"

    base_items = [
        {
            "PK": hotel_pk,
            "SK": "HOTEL#METADATA",
            **HOTEL,
        }
    ]

    for room in ROOM_TYPES:
        base_items.append({
            "PK": hotel_pk,
            "SK": f"TIPO#{room['id']}",
            **room,
        })

    inserted = 0
    already_existed = 0

    for item in base_items:
        if put_if_missing(table, item):
            inserted += 1
        else:
            already_existed += 1

    start_date = get_start_date()

    for room in ROOM_TYPES:
        inventory_pk = f"HOTEL#{HOTEL_ID}#TIPO#{room['id']}"

        for offset in range(DAYS_TO_SEED):
            night = start_date + timedelta(days=offset)

            inventory_item = {
                "PK": inventory_pk,
                "SK": f"NOCHE#{night.isoformat()}",
                "fecha": night.isoformat(),
                "inventario_total": room["inventario_total"],
                "disponibles": room["inventario_total"],
            }

            if put_if_missing(table, inventory_item):
                inserted += 1
            else:
                already_existed += 1

    end_date = start_date + timedelta(days=DAYS_TO_SEED - 1)

    print("Seed terminado.")
    print(f"Tabla: {TABLE_NAME}")
    print(f"Hotel: {HOTEL_ID}")
    print(f"Tipos de habitación: {len(ROOM_TYPES)}")
    print(f"Inventario sembrado desde {start_date} hasta {end_date}")
    print(f"Elementos nuevos: {inserted}")
    print(f"Elementos que ya existían y se conservaron: {already_existed}")


if __name__ == "__main__":
    main()