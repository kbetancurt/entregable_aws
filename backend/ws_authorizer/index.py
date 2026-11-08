"""
Lambda authorizer de $connect para la API WebSocket (sección 7.2).

Un Cognito Authorizer no se aplica a una API WebSocket y el navegador no puede
mandar encabezados, así que el access token de Cognito llega como parámetro
de consulta: wss://.../prod?token=<access token>.

El token se valida contra Cognito con GetUser (verifica firma, vencimiento y
que no haya sido revocado). Nunca se escribe el token en los logs.
"""
import base64
import json
import os

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
HOTEL_ID = os.getenv("HOTEL_ID", "aloft-mvd")

cognito = boto3.client("cognito-idp", region_name=AWS_REGION)


def _token_claims(token):
    """Lee el payload del JWT. Solo se usa DESPUÉS de que Cognito lo validó."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _policy(effect, method_arn, principal="anonimo", context=None):
    policy = {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": method_arn,
            }],
        },
    }
    if context:
        policy["context"] = context
    return policy


def lambda_handler(event, context):
    method_arn = event["methodArn"]
    token = (event.get("queryStringParameters") or {}).get("token")

    if not token:
        print("[ws_authorizer] Conexión rechazada: falta el token.")
        return _policy("Deny", method_arn)

    try:
        user = cognito.get_user(AccessToken=token)
        claims = _token_claims(token)
    except ClientError as error:
        print(f"[ws_authorizer] Token rechazado: {error.response['Error']['Code']}")
        return _policy("Deny", method_arn)
    except (ValueError, IndexError):
        print("[ws_authorizer] Token con formato inválido.")
        return _policy("Deny", method_arn)

    attributes = {a["Name"]: a["Value"] for a in user.get("UserAttributes", [])}
    groups = claims.get("cognito:groups") or []
    role = "admin" if "admin" in groups else "cliente"

    # La conexión registra usuario, rol y hotel (secciones 6.6 y 9).
    return _policy("Allow", method_arn, principal=attributes.get("sub", "desconocido"), context={
        "usuario_id": attributes.get("sub", ""),
        "rol": role,
        "hotel_id": attributes.get("custom:hotelId") or HOTEL_ID,
    })
