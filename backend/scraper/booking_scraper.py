import os
import asyncio
import json
import tempfile
import shutil
import re
import time

from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

from vision_analyzer_web import analyze_image_async

# En Lambda no hay sandbox de usuario ni /dev/shm grande, y no hay GPU.
CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


# ==============================================================
# UTILIDADES
# ==============================================================

def ensure_dir(path: str):
    """
    Crea un directorio si no existe.
    """
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# ==============================================================
# URL DIRECTA DE BOOKING
# ==============================================================

def build_booking_hotel_url(
    hotel_name: str,
    country_code: str,
    checkin: str,
    checkout: str,
    currency: str = "USD",
    lang: str = "en-us",
):
    """
    Construye la URL directa de un hotel en Booking.com.
    """

    name_for_slug = re.sub(
        r"[\s/&']+",
        "-",
        hotel_name.lower().strip().replace("’", "")
    ).strip("-")

    if name_for_slug.endswith("-hotel"):
        name_for_slug = name_for_slug[:-6]

    url = (
        f"https://www.booking.com/hotel/"
        f"{country_code}/{name_for_slug}.html"
        f"?checkin={checkin}"
        f"&checkout={checkout}"
        f"&group_adults=2"
        f"&group_children=0"
        f"&no_rooms=1"
        f"&selected_currency={currency}"
        f"&lang={lang}"
    )

    return url


# ==============================================================
# URL DE BÚSQUEDA
# ==============================================================

def build_booking_search_url(
    hotel_name: str,
    checkin: str,
    checkout: str,
    currency: str = "USD",
    lang: str = "en-us",
):
    """
    Construye una URL de búsqueda de Booking.com como FALLBACK.
    """

    ci_y, ci_m, ci_d = checkin.split("-")
    co_y, co_m, co_d = checkout.split("-")

    params = {
        "ss": hotel_name,

        "checkin_year": ci_y,
        "checkin_month": ci_m,
        "checkin_monthday": ci_d,

        "checkout_year": co_y,
        "checkout_month": co_m,
        "checkout_monthday": co_d,

        "group_adults": 2,
        "group_children": 0,
        "no_rooms": 1,

        "selected_currency": currency,
        "lang": lang,
    }

    return (
        "https://www.booking.com/searchresults.html?"
        + urlencode(params)
    )


# ==============================================================
# PROMPT GEMINI
# ==============================================================

PROMPT_BOOKING_EXTRACT = """
You are an expert data extractor for hotel websites.

Analyze the provided screenshot of a Booking.com hotel page.

Your tasks:

1. Extract Amenities:
   Find the list of "Facilities" or similar sections
   and extract all listed amenities. Write each amenity name in Spanish.

2. Extract Room Rates:
   Find the "Availability" section that lists room types and prices.

   For each available room option:

   a. Extract the room name.

   b. Extract the base price.
      Example: "US$506"

   c. Extract taxes and charges.
      Example: "+ US$94 taxes and charges"

      If taxes and charges are not explicitly shown,
      use 0.

   d. Calculate total_price:
      base_price + taxes

IMPORTANT:

- Prices must be numbers.
- Do not include currency symbols.
- Do not invent prices.
- Only extract information that is actually visible.
- The same room appears in several rows. Only extract rows for 2 guests (two person icons in the "Number of guests" column). Skip rows with a single person icon: they are priced for 1 guest.

Output ONLY valid JSON.

The JSON must contain:

{
    "amenities": [],
    "rates": []
}

Each rate must contain:

{
    "room_name": "...",
    "base_price": 0,
    "taxes": 0,
    "total_price": 0
}

Example:

{
    "amenities": [
        "Indoor swimming pool",
        "Free WiFi",
        "Spa and wellness centre"
    ],
    "rates": [
        {
            "room_name": "Deluxe King Room",
            "base_price": 506,
            "taxes": 94,
            "total_price": 600
        }
    ]
}
"""


# ==============================================================
# EXTRACCIÓN DESDE SCREENSHOT
# ==============================================================

async def extract_booking_data_from_screenshot(screenshot_path: str):
    """
    Envía el screenshot a Gemini y obtiene amenities + rates.
    """
    result = await analyze_image_async(
        f"file://{screenshot_path}",
        PROMPT_BOOKING_EXTRACT
    )

    return result


# ==============================================================
# SCRAPING DIRECTO
# ==============================================================

async def analyze_booking_page_direct(
    hotel_name: str,
    country_code: str,
    checkin: str,
    checkout: str,
    headless: bool = True,
    debug: bool = False,
    debug_root: str = "debug_booking",
):
    url = build_booking_hotel_url(
        hotel_name=hotel_name,
        country_code=country_code,
        checkin=checkin,
        checkout=checkout,
    )

    tmpdir = tempfile.mkdtemp(prefix="booking_capture_")
    debug_dir = None

    try:
        if debug:
            ts = int(time.time())
            safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", hotel_name).strip("_").lower()
            debug_dir = os.path.join(debug_root, f"{safe_name}_{ts}")
            ensure_dir(debug_dir)

        screenshot_path = os.path.join(
            debug_dir if debug else tmpdir,
            "hotel_page_direct.png"
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=CHROMIUM_ARGS
            )

            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Aceptar cookies si aparece el modal
                try:
                    cookie_button = page.locator("#onetrust-accept-btn-handler").first
                    if await cookie_button.is_visible(timeout=5000):
                        await cookie_button.click()
                except Exception:
                    pass

                await page.wait_for_timeout(3000)

                # Si redirige a una lista de resultados, el enlace directo falló
                property_card = page.locator('[data-testid="property-card"]').first
                if await property_card.is_visible(timeout=3000):
                    raise Exception("Direct URL redirected to search results")

                # Hacemos scroll progresivo para cargar elementos lazy-load
                for _ in range(5):
                    await page.mouse.wheel(0, 1200)
                    await asyncio.sleep(1)

                await page.screenshot(path=screenshot_path, full_page=True)

            finally:
                await context.close()
                await browser.close()

        # Análisis con Gemini
        result = await extract_booking_data_from_screenshot(screenshot_path)

        if not result.get("success", True):
            raise Exception(result.get("reason", "Gemini analysis failed"))

        rates = result.get("rates", [])
        prices = [float(r.get("total_price")) for r in rates if isinstance(r.get("total_price"), (int, float))]
        lowest_price = min(prices) if prices else None  # sin tarifas NO es precio 0

        output = {
            "hotel_name": hotel_name,
            "country_code": country_code,
            "checkin": checkin,
            "checkout": checkout,
            "url": url,
            "results": result,
            "lowest_price_per_night": lowest_price,
            "method": "direct",
        }

        if debug and debug_dir:
            output["debug_dir"] = os.path.abspath(debug_dir)

        return output

    except Exception:
        return None

    finally:
        if not debug and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)


# ==============================================================
# FALLBACK: BÚSQUEDA POR NOMBRE
# ==============================================================

async def analyze_booking_page_by_search(
    hotel_name: str,
    checkin: str,
    checkout: str,
    currency: str = "USD",
    headless: bool = True,
    debug: bool = False,
    debug_root: str = "debug_booking",
):
    search_url = build_booking_search_url(
        hotel_name=hotel_name,
        checkin=checkin,
        checkout=checkout,
        currency=currency,
    )

    tmpdir = tempfile.mkdtemp(prefix="booking_capture_")
    debug_dir = None

    try:
        if debug:
            ts = int(time.time())
            safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", hotel_name).strip("_").lower()
            debug_dir = os.path.join(debug_root, f"{safe_name}_{ts}")
            ensure_dir(debug_dir)

        screenshot_path = os.path.join(
            debug_dir if debug else tmpdir,
            "hotel_page_search.png"
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=CHROMIUM_ARGS
            )

            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            page = await context.new_page()

            try:
                await page.goto(search_url, wait_until="networkidle", timeout=90000)

                try:
                    cookie_button = page.locator("#onetrust-accept-btn-handler").first
                    if await cookie_button.is_visible(timeout=5000):
                        await cookie_button.click()
                except Exception:
                    pass

                await page.wait_for_selector('[data-testid="property-card"]', timeout=30000)
                card = page.locator('[data-testid="property-card"]').first
                link = card.locator('a[href*="/hotel/"]').first
                href = await link.get_attribute("href")

                if not href:
                    raise Exception("Could not get hotel link")

                if href.startswith("/"):
                    href = "https://www.booking.com" + href

                await page.goto(href, wait_until="networkidle", timeout=90000)

                try:
                    cookie_button = page.locator("#onetrust-accept-btn-handler").first
                    if await cookie_button.is_visible(timeout=5000):
                        await cookie_button.click()
                except Exception:
                    pass

                for _ in range(5):
                    await page.mouse.wheel(0, 1200)
                    await asyncio.sleep(1)

                await page.screenshot(path=screenshot_path, full_page=True)

            finally:
                await context.close()
                await browser.close()

        result = await extract_booking_data_from_screenshot(screenshot_path)

        if not result.get("success", True):
            return {
                "error": result.get("reason", "Gemini analysis failed"),
                "search_query": hotel_name,
            }

        rates = result.get("rates", [])
        prices = [float(r.get("total_price")) for r in rates if isinstance(r.get("total_price"), (int, float))]
        lowest_price = min(prices) if prices else None  # sin tarifas NO es precio 0

        output = {
            "hotel_name": hotel_name,
            "checkin": checkin,
            "checkout": checkout,
            "url": href,
            "results": result,
            "lowest_price_per_night": lowest_price,
            "method": "search_fallback",
        }

        if debug and debug_dir:
            output["debug_dir"] = os.path.abspath(debug_dir)

        return output

    except Exception as e:
        return {
            "error": str(e),
            "search_query": hotel_name,
            "method": "search_fallback",
        }

    finally:
        if not debug and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)


# ==============================================================
# FUNCIÓN PRINCIPAL
# ==============================================================

async def booking_data(
    hotel_name: str,
    country_code: str,
    checkin: str,
    debug: bool = False,
    debug_root: str = "debug_booking",
):
    if not hotel_name:
        return {"error": "hotel_name is required"}
    if not country_code:
        return {"error": "country_code is required"}
    if not checkin:
        return {"error": "checkin is required"}

    try:
        checkin_date = datetime.strptime(checkin, "%Y-%m-%d")
    except ValueError:
        return {"error": "Invalid date format. Expected YYYY-MM-DD."}

    checkout_date = checkin_date + timedelta(days=1)
    checkout = checkout_date.strftime("%Y-%m-%d")

    # Método 1: Directo
    direct_result = await analyze_booking_page_direct(
        hotel_name=hotel_name,
        country_code=country_code,
        checkin=checkin,
        checkout=checkout,
        debug=debug,
        debug_root=debug_root,
    )

    if direct_result is not None:
        return direct_result

    # Método 2: Fallback
    search_result = await analyze_booking_page_by_search(
        hotel_name=hotel_name,
        checkin=checkin,
        checkout=checkout,
        debug=debug,
        debug_root=debug_root,
    )

    return search_result