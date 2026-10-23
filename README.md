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

