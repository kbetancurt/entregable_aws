"""
Invoca la Lambda de reserva contra DynamoDB Local (sin AWS).

    docker run -p 8000:8000 amazon/dynamodb-local
    python scripts/seed.py
    python scripts/invoke_reserva_local.py --tipo KING-RIVER --noches 2 --repetir 3

Sin EVENTS_QUEUE_URL los eventos a SQS se omiten (solo se imprime un aviso).
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("DYNAMODB_ENDPOINT", "http://localhost:8000")
os.environ.setdefault("AWS_REGION", "us-east-1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "reserva"))

import index  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tipo", default="KING-RIVER")
    p.add_argument("--cantidad", type=int, default=1)
    p.add_argument("--desde", type=int, default=1, help="días desde hoy")
    p.add_argument("--noches", type=int, default=2)
    p.add_argument("--huespedes", type=int)
    p.add_argument("--usuario", default="usuario-local")
    p.add_argument("--repetir", type=int, default=1, help="cuántas reservas seguidas")
    a = p.parse_args()

    entrada = date.today() + timedelta(days=a.desde)
    body = {
        "entrada": entrada.isoformat(),
        "salida": (entrada + timedelta(days=a.noches)).isoformat(),
        "habitaciones": [{"tipo_id": a.tipo, "cantidad": a.cantidad}],
    }
    if a.huespedes:
        body["huespedes"] = a.huespedes

    event = {
        "body": json.dumps(body),
        "headers": {},
        # Simula el claim que API Gateway agrega tras validar el JWT de Cognito.
        "requestContext": {"authorizer": {"claims": {"sub": a.usuario}}},
    }
    for i in range(a.repetir):
        r = index.lambda_handler(event, None)
        print(f"#{i + 1} -> HTTP {r['statusCode']}: {r['body']}")


if __name__ == "__main__":
    main()
