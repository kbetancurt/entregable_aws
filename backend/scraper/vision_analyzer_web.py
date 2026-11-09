import os
import json
import asyncio
import aiohttp
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv

from google import genai
from google.genai import types

load_dotenv()

VISION_MODEL = "gemini-3.5-flash-lite"

# Inicializar cliente oficial leyendo la API KEY desde las variables de entorno
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


async def analyze_image_async(image_url: str, prompt: str, max_retries: int = 3):
    """
    Analiza una imagen usando Gemini Vision de forma asíncrona.

    image_url:
        - Puede ser una URL HTTP/HTTPS
        - Puede ser un archivo local con formato file:///ruta/archivo.png

    Retorna:
        dict con el JSON generado por Gemini.
    """
    if not image_url:
        print("Error: No se proporcionó una URL de imagen.")
        return {
            "success": False,
            "reason": "image_url_missing"
        }

    image = None

    try:
        # ==========================================================
        # CARGAR IMAGEN
        # ==========================================================
        if image_url.startswith("file://"):
            local_path = image_url[len("file://"):]

            if not os.path.exists(local_path):
                print(f"Error: Archivo local no encontrado en la ruta {local_path}")
                return {
                    "success": False,
                    "reason": f"file_not_found: {local_path}"
                }

            image = await asyncio.to_thread(Image.open, local_path)
            image.load()

        else:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        print(f"Error descargando imagen: HTTP {resp.status}")
                        return {
                            "success": False,
                            "reason": f"image_download_error: HTTP {resp.status}"
                        }
                    content = await resp.read()

            image = await asyncio.to_thread(Image.open, BytesIO(content))
            image.load()

        # ==========================================================
        # GEMINI API CON REINTENTOS Y MANEJO DE ERRORES (429 / 503)
        # ==========================================================
        response = None
        for attempt in range(max_retries):
            try:
                response = await client.aio.models.generate_content(
                    model=VISION_MODEL,
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                break  # Si la llamada tiene éxito, salimos del bucle de reintentos
                
            except Exception as api_error:
                error_str = str(api_error)
                print(f"[Intento {attempt + 1}/{max_retries}] Error en Gemini API: {error_str}")
                
                # Si el error es de cuota (429) o servidor ocupado (503), aplicamos la pausa
                if ("429" in error_str or "503" in error_str) and attempt < max_retries - 1:
                    wait_time = 4 ** attempt  # Esperará 1s, luego 4s...
                    print(f"Pausando {wait_time} segundos antes de reintentar para recuperar la cuota...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Si es otro tipo de error (ej. 404, 400) o se acabaron los intentos, lanzamos la excepción final
                    raise api_error

        # ==========================================================
        # EXTRAER JSON
        # ==========================================================
        if not response or not response.text:
            print("Error: Gemini devolvió una respuesta vacía.")
            return {
                "success": False,
                "reason": "gemini_empty_response"
            }

        text = response.text.strip()

        # Limpiar tags Markdown en caso de que vengan incluidos
        if text.startswith("```json"):
            text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
        elif text.startswith("```"):
            text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

        text = text.strip()

        parsed_json = json.loads(text)

        if isinstance(parsed_json, dict):
            parsed_json["success"] = True
            return parsed_json

        return {
            "success": True,
            "data": parsed_json
        }

    except json.JSONDecodeError:
        print("Error: Gemini no devolvió un JSON válido. Respuesta recibida:")
        print(getattr(response, "text", "Sin texto"))
        return {
            "success": False,
            "reason": "json_parse_error",
            "raw": getattr(response, "text", "")
        }

    except Exception as e:
        print(f"Fallo definitivo al procesar con Gemini: {str(e)}")
        return {
            "success": False,
            "reason": f"gemini_api_error: {str(e)}"
        }

    finally:
        if image is not None:
            try:
                image.close()
            except Exception:
                pass