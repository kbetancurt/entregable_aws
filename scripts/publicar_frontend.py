"""
Compila el frontend con las direcciones del stack y lo sube al bucket S3.

Toma de las salidas del stack la URL de la API, la del WebSocket y el client
id de Cognito, las pasa a Vite como variables VITE_* y sube frontend/dist.

Uso:
    python scripts/publicar_frontend.py                  # stack "reservas-hoteleras"
    python scripts/publicar_frontend.py --stack otro-nombre
    python scripts/publicar_frontend.py --sin-build      # sube el dist existente
"""
import argparse
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def stack_outputs(stack_name):
    cloudformation = boto3.client("cloudformation", region_name=AWS_REGION)
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def build(outputs):
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise SystemExit("No se encontró npm. Instalá Node.js 18 o superior.")

    env = {
        **os.environ,
        "VITE_REGION": AWS_REGION,
        "VITE_API_URL": outputs["RestApiUrl"],
        "VITE_WS_URL": outputs["WebSocketUrl"],
        "VITE_COGNITO_CLIENT_ID": outputs["UserPoolClientId"],
    }
    if not (FRONTEND / "node_modules").exists():
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, env=env, check=True)


def upload(bucket):
    s3 = boto3.client("s3", region_name=AWS_REGION)
    dist = FRONTEND / "dist"
    if not dist.exists():
        raise SystemExit("No existe frontend/dist. Corré el script sin --sin-build.")

    published = set()
    for path in dist.rglob("*"):
        if not path.is_file():
            continue
        key = path.relative_to(dist).as_posix()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        # index.html sin caché; los assets llevan hash en el nombre.
        cache = "no-cache" if key == "index.html" else "public, max-age=31536000, immutable"
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": cache})
        published.add(key)
        print("subido", key)

    # Borra los assets de compilaciones anteriores.
    old = [
        {"Key": o["Key"]}
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for o in page.get("Contents", [])
        if o["Key"] not in published
    ]
    for i in range(0, len(old), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": old[i:i + 1000]})
    if old:
        print(f"borrados {len(old)} archivos viejos")


def main():
    parser = argparse.ArgumentParser(description="Compila y publica el frontend en S3.")
    parser.add_argument("--stack", default=os.environ.get("STACK_NAME", "reservas-hoteleras"))
    parser.add_argument("--sin-build", action="store_true")
    args = parser.parse_args()

    outputs = stack_outputs(args.stack)
    if not args.sin_build:
        build(outputs)
    upload(outputs["FrontendBucketName"])
    print("Frontend publicado:", outputs["FrontendUrl"])


if __name__ == "__main__":
    main()
