// Fotos del sitio. Los archivos viven en frontend/public/img/ y se sirven desde la raíz del sitio:
//   img/hotel/portada.jpg            portada de la página principal
//   img/hotel/exterior.jpg           sección "El hotel"
//   img/habitaciones/<tipo_id>-1.jpg  hasta 4 por tipo: <tipo_id>-1.jpg, -2.jpg, -3.jpg, -4.jpg
//                                    (KING-ALOFT, TWIN-TWIN, QUEEN-QUEEN-CITY, KING-RIVER, QUEEN-QUEEN-RIVER)
//                                    La -1 es la principal; las demás se ven al pasar la galería.
// Si falta un archivo, la interfaz muestra el degradado de siempre: nada se rompe.
const base = import.meta.env.BASE_URL;

export const FOTOS_HOTEL = {
  portada: `${base}img/hotel/portada.jpg`,
  exterior: `${base}img/hotel/exterior.jpg`,
};

export const MAX_FOTOS_HABITACION = 4;
export const fotosHabitacion = tipoId =>
  Array.from({ length: MAX_FOTOS_HABITACION }, (_, i) => `${base}img/habitaciones/${tipoId}-${i + 1}.jpg`);
export const fotoHabitacion = tipoId => fotosHabitacion(tipoId)[0];
