"""
Carga los datos de la demo en la tabla de DynamoDB desplegada en AWS.

Usa los mismos datos que scripts/seed.py (hotel, tipos de habitación e
inventario por noche); seed.py sigue siendo el que apunta a DynamoDB Local.
El nombre de la tabla se lee de las salidas del stack de SAM.

Uso:
    python scripts/seed_aws.py                       # stack "reservas-hoteleras"
    python scripts/seed_aws.py --stack otro-nombre
    DAYS_TO_SEED=60 python scripts/seed_aws.py

Como seed.py, solo inserta lo que falta: no pisa el inventario ya consumido.
"""
import argparse
import os
import sys
from datetime import timedelta

import boto3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed  # noqa: E402  (datos de la demo y helpers compartidos)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def stack_outputs(stack_name):
    cloudformation = boto3.client("cloudformation", region_name=AWS_REGION)
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def build_items(hotel_id, start_date, days):
    hotel_pk = f"HOTEL#{hotel_id}"
    items = [{"PK": hotel_pk, "SK": "HOTEL#METADATA", **seed.HOTEL}]

    for room in seed.ROOM_TYPES:
        items.append({"PK": hotel_pk, "SK": f"TIPO#{room['id']}", **room})

    for room in seed.ROOM_TYPES:
        inventory_pk = f"HOTEL#{hotel_id}#TIPO#{room['id']}"
        for offset in range(days):
            night = (start_date + timedelta(days=offset)).isoformat()
            items.append({
                "PK": inventory_pk,
                "SK": f"NOCHE#{night}",
                "fecha": night,
                "inventario_total": room["inventario_total"],
                "disponibles": room["inventario_total"],
            })

    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--stack", default=os.environ.get("STACK_NAME", "reservas-hoteleras"))
    args = parser.parse_args()

    if seed.DAYS_TO_SEED < 1:
        raise ValueError("DAYS_TO_SEED debe ser al menos 1.")

    table_name = stack_outputs(args.stack)["TableName"]
    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)

    start_date = seed.get_start_date()
    inserted = already_existed = 0
    for item in build_items(seed.HOTEL_ID, start_date, seed.DAYS_TO_SEED):
        if seed.put_if_missing(table, item):
            inserted += 1
        else:
            already_existed += 1

    end_date = start_date + timedelta(days=seed.DAYS_TO_SEED - 1)
    print("Seed en AWS terminado.")
    print(f"Stack: {args.stack}  Tabla: {table_name}  Hotel: {seed.HOTEL_ID}")
    print(f"Tipos de habitación: {len(seed.ROOM_TYPES)}")
    print(f"Inventario desde {start_date} hasta {end_date}")
    print(f"Elementos nuevos: {inserted}")
    print(f"Elementos que ya existían y se conservaron: {already_existed}")


if __name__ == "__main__":
    main()
