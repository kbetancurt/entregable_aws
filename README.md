# Sistema de Reservas Hoteleras Serverless

Sistema serverless en AWS para la gestion de reservas de un hotel en tiempo real, junto con un modulo de monitoreo automatizado de precios de la competencia (web scraper) y notificaciones instantaneas mediante WebSockets.

---

## 1. Descripcion General
El proyecto implementa una solucion 100% serverless en AWS diseñada para la operacion de un solo hotel, con una arquitectura lista para escalar a un modelo multi-inquilino (SaaS multi-hotel).

### Funcionalidades Principales
1. Registro y Autenticacion de Usuarios: Gestion de cuentas (clientes y administradores) mediante Amazon Cognito y tokens JWT.
2. Consulta Publica de Disponibilidad: Busqueda abierta por tipo de habitacion y rango de fechas sin requerir autenticacion.
3. Creacion de Reservas Atomicas: Proceso de reserva exclusivo para usuarios autenticados, registrando reservas directamente en estado CONFIRMADA mediante escrituras condicionales en DynamoDB para prevenir la sobreventa.
4. Monitoreo de Competencia y Alerta Push: Ejecucion periodica de un scraper automatizado con Playwright que compara precios de plataformas externas y notifica cambios en tiempo real al panel de administracion mediante WebSockets.

---

## 2. Arquitectura del Sistema
El sistema se divide en tres capas principales:

- Frontend / Distribucion: SPA estatica en React alojada en Amazon S3. En el Learner Lab se sirve como sitio estatico por HTTP, porque el laboratorio no permite conectar CloudFront al bucket; CloudFront queda como diseño.
- API y Autenticacion: API Gateway REST (rutas /habitaciones y /reservas), API Gateway WebSocket (alertas en tiempo real) y Amazon Cognito User Pool.
- Computo y Persistencia: Funciones AWS Lambda (Python), Amazon DynamoDB (Single-Table Design), Amazon SQS (cola de eventos + DLQ), Amazon S3 (respaldo de datos crudos del scraper) y Amazon EventBridge (temporizador del scraper).

El documento completo de arquitectura (diagramas, seguridad, escalabilidad, TCO y resiliencia) esta en [docs/Arquitectura_AWS_Reservas_Hoteleras_v2.docx](docs/Arquitectura_AWS_Reservas_Hoteleras_v2.docx).

### Estado de la implementacion

| Componente | Estado |
| :--- | :--- |
| Lambda Consulta, Lambda Reserva | Implementadas y desplegadas |
| Lambda Avisos, authorizer y conexiones del WebSocket | Implementadas y desplegadas |
| DynamoDB, SQS + DLQ, Cognito, API REST y WebSocket | Definidos en `template.yaml` y desplegados |
| Frontend React conectado a la API, Cognito y WebSocket | Publicado en S3 |
| Lambda Scraper (Playwright + Gemini) y EventBridge | Pendiente |
| CloudFront, WAF, KMS propia, roles IAM por Lambda | Diseño (el Learner Lab no los permite) |

---

## 3. Estructura del Repositorio

```text
entregable_aws/
|-- README.md
|-- template.yaml            # Infraestructura como codigo (AWS SAM, todas las Lambdas con LabRole)
|-- requirements.txt         # Dependencias generales (incluye las del scraper)
|-- requirements-dev.txt     # Dependencias para las pruebas locales
|
|-- backend/                 # Codigo fuente de las funciones AWS Lambda
|   |-- consulta/            # GET /habitaciones (publica)
|   |-- reserva/             # POST /reservas (transaccion sin sobreventa)
|   |-- avisos/              # Consume SQS y notifica por WebSocket
|   |-- ws_authorizer/       # Authorizer de $connect (valida el token de Cognito)
|   |-- ws_conexiones/       # $connect, $disconnect y $default (ping)
|   |-- scraper/             # Pendiente: Playwright + Gemini
|
|-- frontend/                # SPA en React + Vite (api.js, auth.js, avisos.js)
|
|-- scripts/
|   |-- seed.py              # Siembra DynamoDB Local
|   |-- seed_aws.py          # Carga los mismos datos en la tabla desplegada en AWS
|   |-- usuarios_aws.py      # Crea usuarios de prueba en Cognito (cliente o admin)
|   |-- publicar_frontend.py # Compila el frontend con las URLs del stack y lo sube a S3
|   |-- invoke_reserva_local.py
|
|-- tests/                   # Pruebas con pytest + moto
|-- docs/                    # Documento de arquitectura
```
## 4. Requisitos Previos

- Python 3.11 o superior.
- Node.js 18 o superior y npm/pnpm.
- AWS CLI configurado (`aws configure`).
- AWS SAM CLI instalado.
- Docker Desktop (necesario si se compila la Lambda Scraper localmente o en contenedor).

## 5. Guía de Despliegue con AWS SAM

### 5.0. Credenciales del Learner Lab

Cada vez que se inicia el lab: **Start Lab → AWS Details → AWS CLI**, copiar el bloque `[default]` en `~/.aws/credentials` (no subirlo al repo) y comprobar con:

```bash
aws sts get-caller-identity
```

### 5.1. Compilación de la Infraestructura

```bash
sam build
```

Si `sam build` falla con "Acceso denegado" sobre `.aws-sam` (pasa en carpetas de OneDrive), alcanza con volver a ejecutarlo.

### 5.2. Despliegue en AWS

```bash
sam deploy --stack-name reservas-hoteleras --region us-east-1 --resolve-s3 --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
```

Tambien se puede usar `sam deploy --guided` la primera vez; guarda la configuracion en `samconfig.toml` (ignorado por git). Las salidas del stack incluyen `RestApiUrl`, `WebSocketUrl`, `UserPoolClientId` y `FrontendUrl`.

### 5.3. Poblar la Base de Datos y Usuarios de Prueba

```bash
python scripts/seed_aws.py
python scripts/usuarios_aws.py admin@ejemplo.com admin
python scripts/usuarios_aws.py cliente@ejemplo.com cliente
```

`seed_aws.py` carga los mismos datos que `seed.py` (5 tipos de habitacion y 30 noches) y no pisa el inventario ya consumido. `usuarios_aws.py` pide la contraseña por teclado (minimo 8 caracteres, con mayusculas, minusculas y numeros). Si el stack tiene otro nombre, agregar `--stack <nombre>`.

### 5.4. Publicar el Frontend

```bash
python scripts/publicar_frontend.py
```

Compila el frontend con las URLs del stack (variables `VITE_*`), lo sube al bucket y muestra la URL del sitio.

### 5.5. Limpieza de Recursos (Eliminar Infraestructura)

El bucket del frontend tiene que estar vacio antes de borrar el stack:

```bash
aws s3 rm s3://reservas-hoteleras-frontend-<AccountId> --recursive
sam delete --stack-name reservas-hoteleras
```

### 5.6. Pruebas locales (sin AWS)

```bash
pip install -r requirements-dev.txt
pytest tests -v                      # moto simula DynamoDB y SQS en memoria

docker run -p 8000:8000 amazon/dynamodb-local
python scripts/seed.py               # siembra DynamoDB Local
python scripts/invoke_reserva_local.py --tipo KING-RIVER --noches 2 --repetir 3
```

Variables de la Lambda de reserva: `TABLE_NAME`, `HOTEL_ID`, `DYNAMODB_ENDPOINT` (local), `EVENTS_QUEUE_URL` (si falta, los eventos a SQS se omiten).

## 6. Consideraciones de Escalabilidad Multi-Hotel

Aunque la implementación inicial cubre la operación de un solo hotel, el diseño incluye las siguientes decisiones arquitectónicas para facilitar la transición a un modelo SaaS multi-hotel:

- **DynamoDB:** Todas las claves primarias incluyen el identificador del hotel desde el primer día (`PK = HOTEL#<hotelId>` para hotel, tipos y reservas; `PK = HOTEL#<hotelId>#TIPO#<tipoId>` para el inventario por noche).
- **Seguridad:** Inserción del atributo `custom:hotelId` en los tokens JWT de Cognito para validar el aislamiento de datos por inquilino a nivel del autorizador.
- **Notificaciones:** Clasificación de mensajes en la cola SQS por `hotelId` para filtrar conexiones en el API Gateway WebSocket.

---

## 7. Modelado de Datos en DynamoDB (Single-Table Design)

El sistema utiliza una única tabla de DynamoDB (`HotelSystemTable`) para almacenar los datos del hotel. Las reservas se realizan por **tipo de habitación** y rango de fechas. El inventario se controla por noche para evitar confirmar reservas cuando no hay disponibilidad.

Para la demo, el inventario es simulado. Por ejemplo, se puede definir que hay cuatro habitaciones del tipo `KING-RIVER` disponibles cada noche. No se asigna una habitación física específica al huésped.

### 7.1. Patrones de Diseño de Claves

| Entidad | Partition Key (`PK`) | Sort Key (`SK`) | Atributos principales |
| :--- | :--- | :--- | :--- |
| **Metadata del hotel** | `HOTEL#aloft-mvd` | `HOTEL#METADATA` | `nombre`, `direccion`, `descripcion`, `amenities` |
| **Tipo de habitación** | `HOTEL#aloft-mvd` | `TIPO#KING-RIVER` | `nombre`, `capacidad`, `precio_base`, `inventario_total` |
| **Inventario por noche** | `HOTEL#aloft-mvd#TIPO#KING-RIVER` | `NOCHE#2026-10-23` | `inventario_total`, `disponibles` |
| **Reserva** | `HOTEL#aloft-mvd` | `RES#RESV-<id>` | `reserva_id`, `usuario_id`, `habitaciones` (lista de `tipo_id` y `cantidad`), `fecha_inicio`, `fecha_fin`, `noches`, `estado`, `precio_total`, `creada_en` |
| **Alerta OTA / paridad** | `HOTEL#aloft-mvd` | `ALERTA#2026-10-23T14:00:00` | `tipo_habitacion`, `fuente_ota`, `precio_ota`, `precio_directo_web`, `moneda`, `leida` |

### 7.2. Ejemplos de Registros en JSON (Formato DynamoDB)

Los siguientes elementos son ejemplos del contenido almacenado en `HotelSystemTable`.

#### A. Metadata del hotel

```json
{
  "PK": "HOTEL#aloft-mvd",
  "SK": "HOTEL#METADATA",
  "nombre": "Aloft Montevideo Hotel",
  "direccion": "Victor Soliño 350, Punta Carretas, Montevideo",
  "descripcion": "Hotel moderno en el corazón de Punta Carretas, rodeado de gastronomía y tiendas de alto nivel.",
  "amenities": [
    "WiFi gratis de alta velocidad",
    "Piscina techada",
    "Gimnasio Re:charge",
    "W XYZ Bar",
    "Espacios sociales y vivos"
  ]
}
```

#### B. Tipo de habitación

Este elemento describe el tipo de habitación. No tiene un estado general `DISPONIBLE`, ya que la disponibilidad depende de las fechas solicitadas.

```json
{
  "PK": "HOTEL#aloft-mvd",
  "SK": "TIPO#KING-RIVER",
  "nombre": "Habitación King con vista al río",
  "capacidad": 2,
  "precio_base": 140,
  "inventario_total": 4
}
```

#### C. Inventario de un tipo de habitación para una noche

Este ejemplo representa la noche del 23 de octubre de 2026. De las cuatro habitaciones simuladas de este tipo, tres siguen disponibles.

```json
{
  "PK": "HOTEL#aloft-mvd#TIPO#KING-RIVER",
  "SK": "NOCHE#2026-10-23",
  "inventario_total": 4,
  "disponibles": 3
}
```

Se almacena un elemento de inventario por cada noche ofrecida a la venta para cada tipo de habitación. Para una reserva del 23 al 25 de octubre, el sistema comprueba y actualiza las noches del 23 y 24. La fecha de salida, el 25, no cuenta como noche ocupada.

Al confirmar una reserva, la aplicación comprueba que `disponibles` sea mayor que cero para cada noche solicitada y descuenta una unidad de cada una. La creación de la reserva y la actualización del inventario deben realizarse como una operación atómica: si alguna noche no tiene disponibilidad, no se confirma la reserva.

#### D. Reserva

Se almacena un único elemento por estancia, sin importar cuántas noches incluya. La reserva identifica el tipo de habitación, no una habitación física específica.

```json
{
  "PK": "HOTEL#aloft-mvd",
  "SK": "RES#RESV-2026-001",
  "reserva_id": "RESV-2026-001",
  "usuario_id": "usr_998123-abc",
  "habitaciones": [
    { "tipo_id": "KING-RIVER", "cantidad": 1 }
  ],
  "fecha_inicio": "2026-10-23",
  "fecha_fin": "2026-10-25",
  "noches": 2,
  "estado": "CONFIRMADA",
  "precio_total": 280,
  "creada_en": "2026-10-20T10:30:00Z"
}
```

#### E. Alerta de paridad de precios

La alerta utiliza el mismo identificador de tipo de habitación que el sistema para vincular el precio de la OTA con el inventario y la tarifa directa del hotel.

```json
{
  "PK": "HOTEL#aloft-mvd",
  "SK": "ALERTA#2026-10-23T14:00:00",
  "tipo_habitacion": "KING-RIVER",
  "nombre_ota": "Habitación King con vista al río",
  "fuente_ota": "Booking.com",
  "precio_ota": 120,
  "precio_directo_web": 140,
  "moneda": "USD",
  "mensaje": "Booking.com ofrece la habitación King con vista al río a USD 120; la tarifa directa del hotel es USD 140.",
  "leida": false
}
```

### 7.3. Consideraciones de disponibilidad

- `fecha_inicio` es la fecha de llegada y se cuenta como primera noche ocupada.
- `fecha_fin` es la fecha de salida y no se cuenta como noche ocupada.
- El inventario se comprueba para todas las noches de la estancia.
- Si no hay disponibilidad en alguna noche, la reserva completa se rechaza.
- Al cancelar una reserva, se devuelve una unidad al inventario de cada noche reservada.
- Para una futura versión multi-hotel, el identificador del hotel se mantiene en las claves de partición, como `HOTEL#<hotelId>`.
