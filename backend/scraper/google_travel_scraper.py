import os
import asyncio
import json
import tempfile
import shutil
import re
import time
from pathlib import Path
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

from vision_analyzer_web import analyze_image_async

# En Lambda no hay sandbox de usuario ni /dev/shm grande, y no hay GPU.
CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


# ==============================================================
# UTILIDADES
# ==============================================================

def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def format_google_travel_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%a, %b %d")
    except ValueError:
        return date_str


# ==============================================================
# INTERACCIÓN CON NAVEGADOR
# ==============================================================

async def set_google_travel_dates(page, checkin: str, checkout: str):
    checkin_fmt = format_google_travel_date(checkin)
    checkout_fmt = format_google_travel_date(checkout)

    try:
        checkin_input = page.locator('input[aria-label="Check-in"]').first
        await checkin_input.wait_for(state="visible", timeout=7500)
    except Exception as e:
        raise Exception("Could not find visible Check-in input within 6 seconds.") from e

    await checkin_input.fill(checkin_fmt)
    await checkin_input.press("Enter")
    await asyncio.sleep(0.25)

    try:
        checkout_input = page.locator('input[aria-label="Check-out"]').first
        await checkout_input.wait_for(state="visible", timeout=6000)
    except Exception as e:
        raise Exception("Could not find visible Check-out input within 6 seconds.") from e

    await checkout_input.fill(checkout_fmt)
    await checkout_input.press("Enter")
    await asyncio.sleep(0.25)


async def scroll_to_bottom(page):
    """
    Hace scroll tanto en la ventana general como en el contenedor con scroll de Google Travel.
    """
    for _ in range(4):
        await page.mouse.wheel(0, 1500)
        await asyncio.sleep(0.4)


async def click_tab_safe(page, tab_name: str) -> bool:
    try:
        btn = page.get_by_role("tab", name=tab_name, exact=True).first
        await btn.wait_for(state="visible", timeout=6000)
        await btn.click(force=True)
        await page.wait_for_load_state("domcontentloaded", timeout=6000)
        return True
    except Exception:
        return False


# ==============================================================
# FUNCIÓN PRINCIPAL DE EXTRACCIÓN
# ==============================================================

async def google_travel_data(
    hotel_name: str,
    checkin: str,
    city: str = None,
    state_code: str = None,
    headless: bool = True,
    debug: bool = True,
    debug_root: str = "debug_google_travel",
):
    if not hotel_name:
        return {"error": "hotel_name is required"}
    if not checkin:
        return {"error": "checkin is required"}

    search_terms = [hotel_name]
    if city:
        search_terms.append(city)
    if state_code:
        search_terms.append(state_code)
    search_query = " ".join(search_terms)

    try:
        checkin_date = datetime.strptime(checkin, "%Y-%m-%d")
        checkout_date = checkin_date + timedelta(days=1)
        checkout_str = checkout_date.strftime("%Y-%m-%d")
    except ValueError:
        return {"error": "Invalid date format. Expected YYYY-MM-DD."}

    slug = search_query.replace(" ", "%20")
    url = f"https://www.google.com/travel/search?q={slug}&hl=en&curr=USD"

    tmpdir = tempfile.mkdtemp(prefix="gtravel_capture_")
    debug_dir = None

    if debug:
        ts = int(time.time())
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", hotel_name).strip("_").lower()
        debug_dir = os.path.join(debug_root, f"{safe_name}_{ts}")
        ensure_dir(debug_dir)

    screenshot_prices_path = None
    screenshot_about_path = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=CHROMIUM_ARGS
            )
            context = await browser.new_context(
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Aceptar cookies
                try:
                    accept_button = page.get_by_role(
                        "button", name=re.compile("Accept all|Reject all", re.IGNORECASE)
                    ).first
                    await accept_button.wait_for(state="visible", timeout=5000)
                    await accept_button.click()
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                await set_google_travel_dates(page, checkin, checkout_str)

                p_path = os.path.join(debug_dir if debug else tmpdir, "prices_page.png")
                a_path = os.path.join(debug_dir if debug else tmpdir, "about_page.png")

                if await click_tab_safe(page, "Prices"):
                    await asyncio.sleep(1)
                    await scroll_to_bottom(page)
                    await page.screenshot(path=p_path, full_page=True)
                    screenshot_prices_path = p_path

                if await click_tab_safe(page, "About"):
                    await asyncio.sleep(1)
                    await page.screenshot(path=a_path, full_page=True)
                    screenshot_about_path = a_path

            finally:
                await context.close()
                await browser.close()

        # --- ANÁLISIS DE IMÁGENES CON GEMINI ---
        vision_tasks = []

        if screenshot_about_path and os.path.exists(screenshot_about_path):
            PROMPT_AMENITIES = """
            Extract all hotel amenities listed under Internet, Food & drink, Policies & payments, Pools, Parking, etc.
            Write each amenity name in Spanish.
            Return ONLY a JSON object with this key structure:
            {
              "amenities": ["Free Wi-Fi", "Pool", "Restaurant", "Air conditioning"]
            }
            """
            vision_tasks.append(
                ("amenities", analyze_image_async(f"file://{screenshot_about_path}", PROMPT_AMENITIES))
            )

        if screenshot_prices_path and os.path.exists(screenshot_prices_path):
            PROMPT_PRICES = """
            Extract all booking sites/providers and their rates/prices visible in this image.
            Return ONLY a JSON object with this key structure:
            {
              "prices": [
                {"provider": "Marriott.com", "price": "UYU 8,000"},
                {"provider": "Booking.com", "price": "UYU 8,200"}
              ]
            }
            """
            vision_tasks.append(
                ("prices", analyze_image_async(f"file://{screenshot_prices_path}", PROMPT_PRICES))
            )

        if not vision_tasks:
            return {"error": "Could not take any screenshots."}

        # Ejecución en paralelo
        task_names = [t[0] for t in vision_tasks]
        task_coroutines = [t[1] for t in vision_tasks]
        results = await asyncio.gather(*task_coroutines, return_exceptions=True)

        amenities = []
        prices = []

        for name, result in zip(task_names, results):
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue

            # Extrae directamente los datos sin filtrar por result.get("success")
            if name == "amenities":
                amenities = result.get("amenities", [])
            elif name == "prices":
                prices = result.get("prices", [])

        output = {
            "hotel_name": hotel_name,
            "checkin": checkin,
            "checkout": checkout_str,
            "url": url,
            "prices": prices,
            "amenities": amenities,
        }

        if debug and debug_dir:
            output["debug_dir"] = os.path.abspath(debug_dir)

        return output

    except Exception as e:
        return {"error": str(e)}

    finally:
        if not debug and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)