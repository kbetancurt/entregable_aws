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
5. Panel de Alertas para Administradores: campanita con aviso en vivo e historial de alertas (precio mas bajo, cambio de precio, amenities faltantes) con filtros, visible solo para el grupo `admin`.

---

## 2. Arquitectura del Sistema
El sistema se divide en tres capas principales:

- Frontend / Distribucion: SPA estatica en React alojada en Amazon S3. En el Learner Lab se sirve como sitio estatico por HTTP, porque el laboratorio no permite conectar CloudFront al bucket; CloudFront queda como diseño.
- API y Autenticacion: API Gateway REST (rutas /habitaciones, /reservas y /alertas), API Gateway WebSocket (alertas en tiempo real) y Amazon Cognito User Pool.
- Computo y Persistencia: Funciones AWS Lambda (Python), Amazon DynamoDB (Single-Table Design), Amazon SQS (cola de eventos + DLQ), Amazon S3 (respaldo de datos crudos del scraper) y Amazon EventBridge (temporizador del scraper).

El documento completo de arquitectura (diagramas, seguridad, escalabilidad, TCO y resiliencia) esta en [docs/Arquitectura_AWS_Reservas_Hoteleras_v2.docx](docs/Arquitectura_AWS_Reservas_Hoteleras_v2.docx).

### Estado de la implementacion

| Componente | Estado |
| :--- | :--- |
| Lambda Consulta, Lambda Reserva | Implementadas y desplegadas |
| Lambda Avisos, authorizer y conexiones del WebSocket | Implementadas y desplegadas |
| Lambda Alertas (`GET /alertas`, `POST /alertas/leer`) | Implementada y desplegada (solo admin) |
| DynamoDB, SQS + DLQ, Cognito, API REST y WebSocket | Definidos en `template.yaml` y desplegados |
| Frontend React conectado a la API, Cognito y WebSocket (galeria de fotos, campanita y pantalla de Alertas) | Publicado en S3 |
| Lambda Scraper (Playwright + Gemini) y EventBridge | Implementada y desplegada en modo real; probada en AWS con Google Travel (Booking depende de la disponibilidad de la noche). La ejecucion programada por EventBridge todavia no se observo |
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
|   |-- avisos/              # Consume SQS, guarda la alerta y notifica por WebSocket
|   |-- alertas/             # GET /alertas y POST /alertas/leer (solo admin)
|   |-- ws_authorizer/       # Authorizer de $connect (valida el token de Cognito)
|   |-- ws_conexiones/       # $connect, $disconnect y $default (ping)
|   |-- scraper/             # Playwright + Gemini (imagen de contenedor), modo demo/real
|
|-- frontend/                # SPA en React + Vite
|   |-- src/                 # App.jsx, alertas.jsx (campanita e historial), api.js, auth.js, avisos.js, imagenes.js
|   |-- public/img/          # Fotos del hotel y de las habitaciones (ver 5.9)
|
|-- scripts/
|   |-- seed.py              # Siembra DynamoDB Local
|   |-- seed_aws.py          # Carga los mismos datos en la tabla desplegada en AWS
|   |-- usuarios_aws.py      # Crea usuarios de prueba en Cognito (cliente o admin)
|   |-- publicar_frontend.py # Compila el frontend con las URLs del stack y lo sube a S3
|   |-- invoke_reserva_local.py
|   |-- probar_alertas_local.py  # Prueba las Lambdas Alertas y Avisos contra DynamoDB Local
|   |-- servidor_local.py        # API y WebSocket locales para probar el frontend sin AWS
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
aws configure set region us-east-1
aws sts get-caller-identity
```

Las claves del lab vencen al terminar la sesion (unas 4 horas): si aparece `ExpiredToken`, se copia el bloque nuevo. Las variables de entorno `AWS_*` tienen prioridad sobre el archivo.

### 5.1. Compilación de la Infraestructura

```bash
sam build --use-container
```

`--use-container` compila dentro de Docker, asi que no depende de la version de Python instalada (el scraper tambien necesita Docker, porque se despliega como imagen).

Si `sam build` falla con "Acceso denegado" sobre `.aws-sam` (pasa en carpetas de OneDrive), se compila fuera de la carpeta sincronizada. La carpeta de salida tiene que estar un nivel adentro, porque SAM guarda `build.toml` en la carpeta padre:

```bash
sam build --use-container --build-dir C:\sam-build\build
```

En ese caso el deploy usa la plantilla compilada: `sam deploy --template-file C:\sam-build\build\template.yaml ...`.

### 5.2. Despliegue en AWS

```bash
sam deploy --stack-name reservas-hoteleras --region us-east-1 --resolve-s3 --resolve-image-repos --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
```

`--resolve-image-repos` crea el repositorio ECR donde se sube la imagen del scraper. Subir la imagen (mas de 1 GB) es lo que mas tarda; un reinicio de la PC en medio no rompe el stack, se revisa con `aws cloudformation describe-stacks --stack-name reservas-hoteleras --query "Stacks[0].StackStatus"` y se repite el deploy si hace falta.

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

Compila el frontend con las URLs del stack (variables `VITE_*`), lo sube al bucket y muestra la URL del sitio. La URL tambien sale en la salida `FrontendUrl` del stack.

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

**Alertas y Avisos contra DynamoDB Local** (con la tabla sembrada):

```bash
python scripts/probar_alertas_local.py
```

Guarda alertas, las lista como admin, comprueba el 403 para un cliente, las marca como leidas y limpia lo que creo.

**Frontend completo sin AWS ni Cognito:** `scripts/servidor_local.py` expone `/habitaciones`, `/alertas`, `/alertas/leer` y un WebSocket en `ws://127.0.0.1:3001/ws`, usando las Lambdas reales sobre DynamoDB Local. Cada Enter en su terminal genera una alerta de prueba que se guarda y se envia por WebSocket.

```bash
python scripts/servidor_local.py                 # terminal 1 (con DynamoDB Local y el seed)
cd frontend                                      # terminal 2
VITE_API_URL=http://127.0.0.1:3001 VITE_WS_URL=ws://127.0.0.1:3001/ws VITE_COGNITO_CLIENT_ID=local npm run dev
```

Sin Cognito no hay login: en la consola del navegador se simula la sesion de administrador con
`sessionStorage.setItem('aloft-sesion', JSON.stringify({email:'admin@aloft.local',idToken:'x',accessToken:'x',refreshToken:'x',expiresAt:Date.now()+8*3600e3,groups:['admin']})); location.reload()`.
Reservar no funciona en local porque necesita Cognito.

### 5.7. Lambda Scraper

Consulta Booking y Google Travel, compara contra la tarifa propia y publica eventos en SQS (`PRECIO_MENOR_EN_PLATAFORMA`, `CAMBIO_PRECIO`, `AMENITIES_DISTINTAS`). Los precios se comparan en USD con impuestos y solo se alerta cuando cambia el estado, no en cada ejecucion.

Se despliega como imagen de contenedor (Chromium no entra en un zip) y EventBridge la dispara por cron. Modos:

- `demo` (por defecto): lee `backend/scraper/demo/*.json` en vez de abrir los sitios. Es el respaldo si Booking o Google bloquean el acceso desde AWS.
- `real`: usa Playwright y Gemini.

```bash
# Despliegue (requiere Docker; el build y el deploy completos estan en 5.1 y 5.2)
sam deploy ... --parameter-overrides ScraperModo=demo
sam deploy ... --parameter-overrides ScraperModo=real GeminiApiKey=$GEMINI_KEY
```

La clave de Gemini se pasa solo por parametro del deploy (desde una variable de entorno de esa terminal, que se borra despues) y nunca se escribe en el repositorio. Para invocar el scraper a mano (en modo real tarda minutos):

```bash
aws lambda invoke --function-name <ScraperFunction> --cli-read-timeout 900 salida.json
aws logs tail /aws/lambda/<ScraperFunction> --follow     # el grupo de logs existe desde la primera ejecucion
```

El scraper solo mira la noche de hoy + `SCRAPER_OFFSETS_DIAS`. Para provocar una alerta de prueba se carga una tarifa propia alta para esa noche (`PK = HOTEL#aloft-mvd`, `SK = TARIFA#<tipo>#<fecha>`, `precio`) y se invoca de nuevo. Google Travel devuelve precios de varias agencias y se genera un evento por cada una.

Prueba local de una fuente (con DynamoDB Local y el seed cargados):

```bash
cd backend/scraper
export GEMINI_API_KEY=<clave> SCRAPER_MODO=real TABLE_NAME=HotelSystemTable DYNAMODB_ENDPOINT=http://localhost:8000
export AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local AWS_DEFAULT_REGION=us-east-1
python -c "import index; index.lambda_handler({'noches':['2026-11-20'],'fuentes':['booking']}, None)"
```

Con `'fuentes':['google']` se prueba Google Travel; si muestra los precios en UYU, agregar `SCRAPER_TIPOS_DE_CAMBIO=UYU=40`.

Variables principales: `SCRAPER_MODO`, `SCRAPER_OFFSETS_DIAS` (noches desde hoy, por defecto `7`), `SCRAPER_UMBRAL_MENOR` (`0.02`), `SCRAPER_UMBRAL_CAMBIO` (`0.10`), `SCRAPER_TIPOS_DE_CAMBIO`, `SCRAPER_FUENTES`, `EVENTS_QUEUE_URL` (si falta, los eventos no se publican).

### 5.8. Alertas del panel de administracion (campanita)

La Lambda Avisos guarda cada alerta en DynamoDB (`PK = HOTEL#<hotelId>`, `SK = ALERTA#<fecha>#<eventId>`) y la envia por WebSocket a los administradores conectados. La Lambda Alertas permite ver las guardadas, incluidas las que llegaron sin ningun administrador conectado. Ambas rutas requieren el ID token de Cognito y el grupo `admin` (otro usuario recibe 403).

| Ruta | Descripcion |
|---|---|
| `GET /alertas?limite=50` | Alertas mas nuevas primero (limite entre 1 y 100) y cantidad de no leidas |
| `POST /alertas/leer` | Marca como leidas: `{"ids": ["ALERTA#..."]}` o `{"todas": true}` |

Las alertas se borran solas a los 30 dias (atributo `ttl`, configurable con `ALERTA_TTL_DIAS` en la Lambda Avisos).

En el front, solo para administradores:

- **Campanita** en el encabezado: contador de no leidas, ultimas alertas y acceso a "Ver todas las alertas".
- **Aviso emergente** cuando llega una alerta por WebSocket; al tocarlo se abre el detalle.
- **Pantalla de Alertas** (`#/alertas`): historial de hasta 100 alertas agrupadas por dia, filtros por tipo (precio mas bajo, cambio de precio, amenities, disponibilidad) y por sin leer, y "Marcar todas como leidas".
- Las alertas guardadas y las recibidas en vivo se fusionan por identificador, asi que una alerta no se muestra dos veces. Al reconectar el WebSocket se vuelve a pedir la lista.

La home es la misma para clientes y administradores; los clientes no ven la campanita ni el enlace a Alertas.

### 5.9. Fotos del sitio

Las fotos viven en `frontend/public/img/` y se sirven desde la raiz del sitio:

| Archivo | Uso |
|---|---|
| `img/hotel/portada.jpg` | Portada de la pagina principal |
| `img/hotel/exterior.jpg` | Seccion "El hotel" |
| `img/habitaciones/<TIPO>-1.jpg` a `-4.jpg` | Galeria de cada tipo (`KING-ALOFT`, `TWIN-TWIN`, `QUEEN-QUEEN-CITY`, `KING-RIVER`, `QUEEN-QUEEN-RIVER`) |

La `-1` es la principal. Con dos o mas fotos la tarjeta muestra flechas y puntos; las que falten simplemente no aparecen y, si un tipo no tiene ninguna, se ve un degradado. Formato `.jpg`, unos 1600 px de ancho y menos de 400 KB.

### 5.10. Limite de combinaciones en la consulta

`GET /habitaciones` devuelve como maximo `MAX_COMBINACIONES` opciones (por defecto 6), ordenadas por menos habitaciones y menor precio, y descarta las que sobran mas de `MAX_CAPACIDAD_SOBRANTE` lugares (por defecto 2). La respuesta incluye `opciones_totales` con el numero total de combinaciones posibles.

---

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