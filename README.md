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

- Frontend / Distribucion: SPA estatica alojada en Amazon S3 y distribuida via Amazon CloudFront.
- API y Autenticacion: API Gateway REST (rutas /habitaciones y /reservas), API Gateway WebSocket (alertas en tiempo real) y Amazon Cognito User Pool.
- Computo y Persistencia: Funciones AWS Lambda (Python), Amazon DynamoDB (Single-Table Design), Amazon SQS (cola de eventos + DLQ), Amazon S3 (respaldo de datos crudos del scraper) y Amazon EventBridge (temporizador del scraper).

---

## 3. Estructura del Repositorio

```text
hotel-serverless-system/
|-- .gitignore
|-- README.md
|-- template.yaml            # Plantilla de infraestructura como codigo (AWS SAM)
|
|-- backend/                 # Codigo fuente de las funciones AWS Lambda
|   |-- consulta/
|   |   |-- index.py
|   |   |-- requirements.txt
|   |-- reserva/
|   |   |-- index.py
|   |   |-- requirements.txt
|   |-- scraper/
|   |   |-- Dockerfile       # Contenedor para Playwright + Chromium
|   |   |-- index.py
|   |   |-- requirements.txt
|   |-- avisos/
|       |-- index.py
|       |-- requirements.txt
|
|-- frontend/                # Aplicacion SPA (Next.js / React / Tailwind)
|   |-- ...
|
|-- scripts/                 # Scripts auxiliares de desarrollo
    |-- seed.py              # Script para poblar DynamoDB y crear usuarios en Cognito
```
## 4. Requisitos Previos

- Python 3.11 o superior.
- Node.js 18 o superior y npm/pnpm.
- AWS CLI configurado (`aws configure`).
- AWS SAM CLI instalado.
- Docker Desktop (necesario si se compila la Lambda Scraper localmente o en contenedor).

## 5. Guía de Despliegue con AWS SAM

### 5.1. Compilación de la Infraestructura

```bash
sam build
```

### 5.2. Despliegue en AWS

Para el primer despliegue interactivo:

```bash
sam deploy --guided
```

En despliegues posteriores, basta con ejecutar:

```bash
sam deploy
```

### 5.3. Poblar la Base de Datos y Usuarios de Prueba

Una vez completado el despliegue de SAM, ejecutar el script de inicialización:

```bash
python scripts/seed.py
```

### 5.4. Limpieza de Recursos (Eliminar Infraestructura)

Para destruir todos los recursos creados en AWS y evitar consumir créditos adicionales:

```bash
sam delete
```

## 6. Consideraciones de Escalabilidad Multi-Hotel

Aunque la implementación inicial cubre la operación de un solo hotel, el diseño incluye las siguientes decisiones arquitectónicas para facilitar la transición a un modelo SaaS multi-hotel:

- **DynamoDB:** Todas las claves primarias incluyen el identificador del hotel desde el primer día (`PK = HOTEL#<id>#HAB#<id>`).
- **Seguridad:** Inserción del atributo `custom:hotelId` en los tokens JWT de Cognito para validar el aislamiento de datos por inquilino a nivel del autorizador.
- **Notificaciones:** Clasificación de mensajes en la cola SQS por `hotelId` para filtrar conexiones en el API Gateway WebSocket.

---

## 7. Modelado de Datos en DynamoDB (Single-Table Design)

El sistema utiliza una única tabla de DynamoDB (`HotelSystemTable`) para almacenar los datos del hotel. Las reservas se realizan por **tipo de habitación** y rango de fechas. El inventario se controla por noche para evitar confirmar reservas cuando no hay disponibilidad.

Para la demo, el inventario es simulado. Por ejemplo, se puede definir que hay cuatro habitaciones del tipo `KING-CIUDAD` disponibles cada noche. No se asigna una habitación física específica al huésped.

### 7.1. Patrones de Diseño de Claves

| Entidad | Partition Key (`PK`) | Sort Key (`SK`) | Atributos principales |
| :--- | :--- | :--- | :--- |
| **Metadata del hotel** | `HOTEL#aloft-mvd` | `HOTEL#METADATA` | `nombre`, `direccion`, `descripcion`, `amenities` |
| **Tipo de habitación** | `HOTEL#aloft-mvd` | `TIPO#KING-CIUDAD` | `nombre`, `capacidad`, `precio_base`, `inventario_total` |
| **Inventario por noche** | `HOTEL#aloft-mvd#TIPO#KING-CIUDAD` | `NOCHE#2026-10-23` | `inventario_total`, `disponibles` |
| **Reserva** | `HOTEL#aloft-mvd` | `RES#RESV-2026-001` | `usuario_id`, `tipo_habitacion`, `fecha_inicio`, `fecha_fin`, `estado`, `precio_total` |
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
  "SK": "TIPO#KING-CIUDAD",
  "nombre": "Habitación 1 King, Vista a la Ciudad",
  "capacidad": 2,
  "precio_base": 140,
  "inventario_total": 4
}
```

#### C. Inventario de un tipo de habitación para una noche

Este ejemplo representa la noche del 23 de octubre de 2026. De las cuatro habitaciones simuladas de este tipo, tres siguen disponibles.

```json
{
  "PK": "HOTEL#aloft-mvd#TIPO#KING-CIUDAD",
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
  "usuario_id": "usr_998123-abc",
  "tipo_habitacion": "KING-CIUDAD",
  "fecha_inicio": "2026-10-23",
  "fecha_fin": "2026-10-25",
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
  "tipo_habitacion": "KING-CIUDAD",
  "nombre_ota": "Habitación 1 King, Vista a la Ciudad",
  "fuente_ota": "Booking.com",
  "precio_ota": 120,
  "precio_directo_web": 140,
  "moneda": "USD",
  "mensaje": "Booking.com ofrece la habitación King Ciudad a USD 120; la tarifa directa del hotel es USD 140.",
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