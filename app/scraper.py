from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from .utils import ensure_data_dirs, timestamp_str
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .logging_config import get_logger

logger = get_logger("scraper")

ensure_data_dirs()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10),
       retry=retry_if_exception_type(Exception))
def scrape_url(url: str, timeout: int = 30000):
    ts = timestamp_str()
    desktop_path = f"data/screenshots/{ts}_desktop.png"
    mobile_path = f"data/screenshots/{ts}_mobile.png"
    html_content = ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # create a context with a common user-agent to reduce bot blocking
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            context = browser.new_context(user_agent=user_agent, viewport={"width": 1280, "height": 800})
            page = context.new_page()
            logger.info("Navigating to %s (desktop)", url)
            # Navigate and wait for network to be idle
            page.goto(url, timeout=timeout)
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                # best-effort
                logger.debug("networkidle wait failed for %s, continuing", url)
            # Also wait for a main content selector to appear if present
            try:
                page.wait_for_selector('main, body > header, [role=main]', timeout=5000)
            except Exception:
                logger.debug("main selector not found for %s, continuing", url)
            # small pause to allow lazy-loaded content to render
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            # wait up to 6s for images to finish loading (helps avoid early blank screenshots)
            try:
                page.wait_for_function("() => Array.from(document.images).every(i=>i.complete && (i.naturalWidth||0)>0)", timeout=6000)
            except Exception:
                logger.debug("Not all images completed for %s before screenshot; continuing", url)
            page.screenshot(path=desktop_path, full_page=True)
            html_content = page.content()
            # mobile screenshot: create a mobile-like viewport
            page.set_viewport_size({"width": 375, "height": 812})
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
            page.reload(timeout=timeout)
            try:
                page.wait_for_load_state('networkidle', timeout=6000)
            except Exception:
                logger.debug("networkidle wait failed on mobile for %s, continuing", url)
            try:
                page.wait_for_function("() => Array.from(document.images).every(i=>i.complete && (i.naturalWidth||0)>0)", timeout=5000)
            except Exception:
                logger.debug("Not all images completed on mobile for %s before screenshot; continuing", url)
            try:
                page.wait_for_timeout(600)
            except Exception:
                pass
            page.screenshot(path=mobile_path, full_page=True)
            context.close()
            browser.close()
    except PlaywrightTimeoutError as e:
        logger.exception("Playwright timeout for %s: %s", url, e)
        raise
    except Exception as e:
        logger.exception("Error scraping %s: %s", url, e)
        raise

    return {"html": html_content, "screenshot_desktop": desktop_path, "screenshot_mobile": mobile_path}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10),
       retry=retry_if_exception_type(Exception))
def screenshot_url_bytes(url: str, device: str = 'desktop', timeout: int = 30000):
    """Return PNG bytes for the requested url and device viewport without
    persisting files to disk. Use for on-demand ephemeral screenshots.
    device: 'desktop' or 'mobile'"""
    html_content = ""
    img_bytes = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            # default desktop viewport
            viewport = {"width": 1280, "height": 800}
            if device == 'mobile':
                viewport = {"width": 375, "height": 812}
            context = browser.new_context(user_agent=user_agent, viewport=viewport)
            page = context.new_page()
            logger.info("Navigating to %s (%s) for ephemeral screenshot", url, device)
            page.goto(url, timeout=timeout)
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                logger.debug("networkidle wait failed for %s, continuing", url)
            try:
                page.wait_for_selector('main, body > header, [role=main]', timeout=5000)
            except Exception:
                logger.debug("main selector not found for %s, continuing", url)
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            try:
                page.wait_for_function("() => Array.from(document.images).every(i=>i.complete && (i.naturalWidth||0)>0)", timeout=6000)
            except Exception:
                logger.debug("Not all images completed for %s before screenshot; continuing", url)
            # take screenshot as bytes
            img_bytes = page.screenshot(full_page=True)
            html_content = page.content()
            context.close()
            browser.close()
    except Exception as e:
        logger.exception("Error taking ephemeral screenshot for %s: %s", url, e)
        raise

    return {"html": html_content, "screenshot_bytes": img_bytes}
