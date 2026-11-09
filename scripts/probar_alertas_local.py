"""
Prueba local de las Lambdas Alertas y Avisos contra DynamoDB Local.

Antes: DynamoDB Local levantado en el puerto 8000 y la tabla creada (python scripts/seed.py).
Uso (desde la raiz del repo):  python scripts/probar_alertas_local.py

No toca AWS: usa credenciales "local" y borra las alertas de prueba al terminar.
No prueba Cognito ni el envio real por WebSocket (eso solo se ve desplegado).
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DYNAMODB_ENDPOINT", "http://localhost:8000")
os.environ.setdefault("TABLE_NAME", "HotelSystemTable")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.pop("WS_ENDPOINT", None)  # sin conexiones abiertas no hace falta

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "backend" / "alertas"))
import index as alertas  # noqa: E402

sys.path.pop(0)
sys.modules.pop("index")
sys.path.insert(0, str(RAIZ / "backend" / "avisos"))
import index as avisos  # noqa: E402

PK = f"HOTEL#{os.getenv('HOTEL_ID', 'aloft-mvd')}"
tabla = alertas.table
fallos = []


def comprobar(nombre, condicion, detalle=""):
    print(("  OK   " if condicion else "  FALLA ") + nombre + (f"  ->  {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def evento(metodo, ruta, grupos="admin", cuerpo=None, query=None):
    return {
        "httpMethod": metodo, "path": ruta,
        "queryStringParameters": query,
        "body": json.dumps(cuerpo) if cuerpo is not None else None,
        "requestContext": {"authorizer": {"claims": {"cognito:groups": grupos}}},
    }


def cuerpo(resp):
    return json.loads(resp["body"])


def limpiar():
    resp = tabla.query(KeyConditionExpression="PK = :p AND begins_with(SK, :s)",
                       ExpressionAttributeValues={":p": PK, ":s": "ALERTA#"})
    for item in resp["Items"]:
        if item.get("eventId", "").startswith("prueba-"):
            tabla.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
    for i in (1, 2, 3):
        tabla.delete_item(Key={"PK": f"EVENTO#prueba-{i}", "SK": "PROCESADO"})


def mensaje_sqs(n, tipo="PRECIO_MENOR_EN_PLATAFORMA"):
    cuerpo_evento = {
        "eventId": f"prueba-{n}", "hotelId": "aloft-mvd", "tipo": tipo,
        "ocurridoEn": f"2026-11-09T10:0{n}:00+00:00",
        "mensaje": f"Alerta de prueba {n}", "precio": 250.5,
    }
    return {"messageId": f"m{n}", "body": json.dumps(cuerpo_evento)}


limpiar()

print("\n1) Avisos: guarda la alerta aunque no haya nadie conectado")
r = avisos.lambda_handler({"Records": [mensaje_sqs(1), mensaje_sqs(2)]}, None)
comprobar("sin fallos de lote", r["batchItemFailures"] == [], r)
item = tabla.query(KeyConditionExpression="PK = :p AND begins_with(SK, :s)",
                   ExpressionAttributeValues={":p": PK, ":s": "ALERTA#2026-11-09T10:01"})["Items"]
comprobar("alerta 1 guardada", len(item) == 1)
comprobar("alerta con ttl y sin leer", bool(item) and "ttl" in item[0] and item[0]["leida"] is False)

print("\n2) Avisos: un evento repetido no duplica la alerta")
avisos.lambda_handler({"Records": [mensaje_sqs(1)]}, None)
todas = [i for i in tabla.query(KeyConditionExpression="PK = :p AND begins_with(SK, :s)",
         ExpressionAttributeValues={":p": PK, ":s": "ALERTA#"})["Items"] if i["eventId"].startswith("prueba-")]
comprobar("siguen siendo 2", len(todas) == 2, len(todas))

print("\n3) Avisos: mensaje inválido se descarta sin romper")
r = avisos.lambda_handler({"Records": [{"messageId": "x", "body": "no es json"}]}, None)
comprobar("sin fallos de lote", r["batchItemFailures"] == [])

print("\n4) Alertas: GET como admin")
r = alertas.lambda_handler(evento("GET", "/alertas"), None)
datos = cuerpo(r)
mias = [a for a in datos["alertas"] if a["eventId"].startswith("prueba-")]
comprobar("200", r["statusCode"] == 200, r["statusCode"])
comprobar("devuelve las 2 alertas de prueba, la más nueva primero",
          len(mias) == 2 and mias[0]["eventId"] == "prueba-2", [a["eventId"] for a in mias])
comprobar("cuenta no leídas", datos["no_leidas"] >= 2, datos.get("no_leidas"))

print("\n5) Alertas: un cliente no puede verlas")
r = alertas.lambda_handler(evento("GET", "/alertas", grupos="cliente"), None)
comprobar("403", r["statusCode"] == 403, r["statusCode"])

print("\n6) Alertas: marcar una como leída")
id_alerta = mias[0]["id"]
antes = cuerpo(alertas.lambda_handler(evento("GET", "/alertas"), None))["no_leidas"]
r = alertas.lambda_handler(evento("POST", "/alertas/leer", cuerpo={"ids": [id_alerta]}), None)
despues = cuerpo(alertas.lambda_handler(evento("GET", "/alertas"), None))
comprobar("200", r["statusCode"] == 200, r["statusCode"])
comprobar("baja un no leído", despues["no_leidas"] == antes - 1, (antes, despues["no_leidas"]))

print("\n7) Alertas: pedidos inválidos")
r = alertas.lambda_handler(evento("POST", "/alertas/leer", cuerpo={}), None)
comprobar("body sin ids ni 'todas' -> 400", r["statusCode"] == 400, r["statusCode"])
r = alertas.lambda_handler(evento("GET", "/alertas", query={"limite": "500"}), None)
comprobar("límite fuera de rango -> 400", r["statusCode"] == 400, r["statusCode"])

print("\n8) Alertas: marcar todas")
r = alertas.lambda_handler(evento("POST", "/alertas/leer", cuerpo={"todas": True}), None)
despues = cuerpo(alertas.lambda_handler(evento("GET", "/alertas"), None))
comprobar("0 no leídas", r["statusCode"] == 200 and despues["no_leidas"] == 0, despues.get("no_leidas"))

limpiar()
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
sys.exit(1 if fallos else 0)