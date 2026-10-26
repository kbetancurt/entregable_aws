# Frontend — Aloft Montevideo

SPA estática desarrollada con React y Vite. Los nombres de los tipos de habitación y los servicios visibles toman como referencia la información pública de Aloft Montevideo Hotel. Los precios y cantidades disponibles son datos simulados para la demo.

## Ejecutar localmente

Requiere Node.js 20.19+ o 22.12+.

```bash
npm install
npm run dev
```

Vite muestra en la terminal la dirección local para abrir la aplicación.

## Funciones incluidas

- Buscar habitaciones por fechas y cantidad de huéspedes.
- Mostrar cuatro tipos de habitación basados en la oferta publicada del hotel.
- Comprobar el inventario simulado por noche.
- Crear una reserva de demostración por estancia y evitar superar el inventario local.
- Guardar reservas en el almacenamiento local del navegador y borrarlas desde la página.

## Modo local

La interfaz aún no está conectada a las Lambdas, DynamoDB ni Cognito. El inicio de sesión es solo visual; el formulario de reserva usa nombre y correo como identidad de demostración. Las reservas se guardan en el navegador actual y no representan confirmaciones reales.

## Compilar para alojamiento estático

```bash
npm run build
```

Vite genera el sitio en `dist/`, listo para publicarse en un bucket S3. CloudFront deberá enviar las rutas de la SPA a `index.html`.
