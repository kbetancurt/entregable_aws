"""
Crea (o actualiza) un usuario de prueba en el user pool de Cognito del stack
y lo agrega al grupo indicado: cliente o admin (sección 7.2).

A los administradores se les guarda además custom:hotelId (sección 9).
La contraseña la gestiona Cognito; este script no la guarda en ningún lado.

Uso:
    python scripts/usuarios_aws.py admin@ejemplo.com admin
    python scripts/usuarios_aws.py cliente@ejemplo.com cliente --stack otro-nombre

La contraseña se pide por teclado (o se toma de USUARIO_PASSWORD). Debe tener
al menos 8 caracteres, con mayúsculas, minúsculas y números.
"""
import argparse
import getpass
import os

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
HOTEL_ID = os.environ.get("HOTEL_ID", "aloft-mvd")
ROLES = ("cliente", "admin")


def stack_outputs(stack_name):
    cloudformation = boto3.client("cloudformation", region_name=AWS_REGION)
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def upsert_user(cognito, user_pool_id, email, password, role):
    attributes = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
    ]
    if role == "admin":
        attributes.append({"Name": "custom:hotelId", "Value": HOTEL_ID})

    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=attributes,
            MessageAction="SUPPRESS",  # sin correo de invitación
        )
        created = True
    except cognito.exceptions.UsernameExistsException:
        cognito.admin_update_user_attributes(
            UserPoolId=user_pool_id, Username=email, UserAttributes=attributes
        )
        created = False

    # Contraseña definitiva: el usuario puede iniciar sesión directamente.
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id, Username=email, Password=password, Permanent=True
    )

    for group in ROLES:
        if group == role:
            cognito.admin_add_user_to_group(
                UserPoolId=user_pool_id, Username=email, GroupName=group
            )
        else:
            cognito.admin_remove_user_from_group(
                UserPoolId=user_pool_id, Username=email, GroupName=group
            )

    return created


def main():
    parser = argparse.ArgumentParser(description="Crea un usuario de prueba en Cognito.")
    parser.add_argument("email")
    parser.add_argument("rol", choices=ROLES)
    parser.add_argument("--stack", default=os.environ.get("STACK_NAME", "reservas-hoteleras"))
    args = parser.parse_args()

    password = os.environ.get("USUARIO_PASSWORD") or getpass.getpass("Contraseña: ")
    user_pool_id = stack_outputs(args.stack)["UserPoolId"]
    cognito = boto3.client("cognito-idp", region_name=AWS_REGION)

    created = upsert_user(cognito, user_pool_id, args.email, password, args.rol)
    print(f"Usuario {args.email} {'creado' if created else 'actualizado'} en el grupo {args.rol}.")


if __name__ == "__main__":
    main()
