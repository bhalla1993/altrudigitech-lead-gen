import os
from typing import Dict
from PIL import Image, ImageFilter, ImageStat
import numpy as np
import openai
import httpx
import traceback
import json
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .logging_config import get_logger
import threading
import time
from collections import deque

logger = get_logger("analyzer")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY")
if OPENAI_API_KEY:
    try:
        openai.api_key = OPENAI_API_KEY
    except Exception:
        # some openai installs may not expect assignment; ignore
        pass


# Simple thread-safe rate limiter (sliding window)
class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.lock = threading.Lock()
        self.calls = deque()

    def acquire(self):
        with self.lock:
            now = time.time()
            # Remove expired timestamps
            while self.calls and self.calls[0] <= now - self.period:
                self.calls.popleft()
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return 0.0
            # Need to wait until the oldest call expires
            wait_for = (self.calls[0] + self.period) - now
        # release lock while sleeping
        if wait_for > 0:
            time.sleep(wait_for)
        # Try again recursively (small contention ok)
        return self.acquire()


# Configure rate limiter from env: OPENAI_RATE_LIMIT_PER_MIN (default 60)
try:
    rl_per_min = int(os.getenv("OPENAI_RATE_LIMIT_PER_MIN", "60"))
except Exception:
    rl_per_min = 60
openai_rate_limiter = RateLimiter(max_calls=rl_per_min, period=60.0)


def _compute_image_features(path: str) -> Dict[str, float]:
    try:
        im = Image.open(path).convert("RGB")
    except Exception as e:
        logger.exception("Failed to open image %s: %s", path, e)
        # Return conservative default features
        return {
            "brightness": 128.0,
            "contrast": 32.0,
            "colorfulness": 10.0,
            "edge_density": 0.5,
            "hist_norm": np.ones(768, dtype=np.float32) / 768.0,
        }
    im_thumb = im.copy()
    im_thumb.thumbnail((640, 640))

    gray = im_thumb.convert("L")
    stat = ImageStat.Stat(gray)
    brightness = float(stat.mean[0])  # 0-255
    contrast = float(stat.stddev[0])

    arr = np.array(im_thumb).astype(np.float32)
    if arr.ndim != 3 or arr.shape[2] < 3:
        # Unexpected image shape — fallback defaults
        logger.warning("Unexpected image shape for %s: %s", path, arr.shape)
        return {
            "brightness": 128.0,
            "contrast": 32.0,
            "colorfulness": 10.0,
            "edge_density": 0.5,
            "hist_norm": np.ones(768, dtype=np.float32) / 768.0,
        }
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    rg = np.abs(R - G)
    yb = np.abs(0.5 * (R + G) - B)
    colorfulness = float(np.mean(np.sqrt(rg ** 2 + yb ** 2)))

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_arr = np.array(edges)
    edge_density = float(np.mean(edge_arr > 30))

    hist = im_thumb.histogram()
    hist = np.array(hist).astype(np.float32)
    # Ensure histogram length is 768 (RGB) — pad or trim if necessary
    if hist.size < 768:
        hist = np.pad(hist, (0, 768 - hist.size), mode="constant")
    elif hist.size > 768:
        hist = hist[:768]
    hist_sum = float(hist.sum()) if hist.sum() else 1.0
    hist_norm = hist / hist_sum

    return {
        "brightness": brightness,
        "contrast": contrast,
        "colorfulness": colorfulness,
        "edge_density": edge_density,
        "hist_norm": hist_norm,
    }


def _histogram_distance(h1: np.ndarray, h2: np.ndarray) -> float:
    # Use L1 distance normalized
    return float(np.sum(np.abs(h1 - h2))) / 2.0


def _normalize(value: float, vmax: float) -> float:
    return max(0.0, min(1.0, value / (vmax if vmax else 1.0)))


def analyze_images(screenshot_desktop: str, screenshot_mobile: str, html: str = ""):
    """Deterministic analyzer producing per-category scores and a weighted overall score.

    Returns a dict with keys: score (1-10 int), reason, explanation, suggestion, features, issues, categories
    """
    d_feat = _compute_image_features(screenshot_desktop)
    m_feat = _compute_image_features(screenshot_mobile)

    # Basic derived metrics
    hist_dist = _histogram_distance(d_feat["hist_norm"], m_feat["hist_norm"])  # 0..1-ish
    responsiveness = 1.0 - _normalize(hist_dist, 1.0)
    brightness = float(d_feat["brightness"])
    contrast = float(d_feat["contrast"])
    colorfulness = float(d_feat["colorfulness"])
    edge_density = float(d_feat["edge_density"])  # 0..1

    # HTML probes (lightweight string checks)
    h = (html or "").lower()
    probes = {
        "has_viewport": 1 if ("<meta name=\"viewport\"" in h or "<meta name='viewport'" in h) else 0,
        "has_h1": 1 if "<h1" in h else 0,
        "has_form": 1 if "<form" in h else 0,
        "has_nav": 1 if "<nav" in h else 0,
        "has_title": 1 if "<title" in h else 0,
        "has_meta_description": 1 if "<meta name=\"description\"" in h or "<meta name='description'" in h else 0,
        "has_favicon": 1 if ("favicon" in h or "rel=\"icon\"" in h or "rel='icon'" in h) else 0,
        "has_logo": 1 if "logo" in h or "aria-label=\"logo\"" in h or "<svg" in h else 0,
        "has_services": 1 if "service" in h or "services" in h else 0,
    }

    # count scripts and images roughly
    scripts = h.count("<script")
    imgs = h.count("<img")
    alts = h.count("alt=")

    # file sizes
    try:
        ds = os.path.getsize(screenshot_desktop) if screenshot_desktop and os.path.exists(screenshot_desktop) else 0
        ms = os.path.getsize(screenshot_mobile) if screenshot_mobile and os.path.exists(screenshot_mobile) else 0
    except Exception:
        ds = ms = 0

    features = {
        "responsiveness": round(responsiveness, 3),
        "brightness": round(brightness, 1),
        "contrast": round(contrast, 1),
        "colorfulness": round(colorfulness, 1),
        "edge_density": round(edge_density, 3),
        "html_length": len(html or ""),
        "scripts": scripts,
        "imgs": imgs,
        "alts": alts,
        "screenshot_desktop_kb": int(ds / 1024),
        "screenshot_mobile_kb": int(ms / 1024),
    }

    issues = []

    # Category scoring helpers: return 0..10
    def score_mobile():
        s = 0.0
        # viewport meta important
        s += 3.0 * probes["has_viewport"]
        # histogram similarity is good for layout parity
        s += 4.0 * responsiveness
        # penalize blank screenshots
        if edge_density < 0.02:
            s -= 2.0
            issues.append({"code": "mobile_blank", "msg": "Mobile/desktop screenshots differ or page blank on mobile", "suggestion": "Ensure responsive layout and wait for content before screenshot."})
        # penalize overly large mobile image
        if features["screenshot_mobile_kb"] > 300:
            s -= 1.0
            issues.append({"code": "mobile_heavy_images", "msg": f"Large mobile images ({features['screenshot_mobile_kb']} KB)", "suggestion": "Optimize mobile images and use responsive srcset."})
        return max(0.0, min(10.0, s))

    def score_modern_design():
        s = 5.0
        # colorfulness and contrast add to perceived modern design
        s += 2.0 * _normalize(colorfulness, 80.0)
        s += 2.0 * _normalize(contrast, 128.0)
        # presence of webfonts (google fonts) hints
        if "fonts.googleapis.com" in h:
            s += 1.0
        else:
            s -= 1.0
        return max(0.0, min(10.0, s))

    def score_page_speed():
        s = 6.0
        # many scripts reduce score
        if scripts > 10:
            s -= 2.0
            issues.append({"code": "many_scripts", "msg": f"Many external scripts detected ({scripts})", "suggestion": "Audit and defer non-critical scripts."})
        # large images reduce score
        if features["screenshot_desktop_kb"] > 500:
            s -= 2.0
            issues.append({"code": "large_images", "msg": f"Large images detected ({features['screenshot_desktop_kb']} KB)", "suggestion": "Compress hero images and use modern formats."})
        return max(0.0, min(10.0, s))

    def score_navigation():
        s = 5.0
        if probes["has_nav"]:
            s += 2.0
        # CTA detection (reuse simple keyword probe)
        if any(kw in h for kw in ("contact","book","signup","get started","buy","order","call","quote")):
            s += 2.0
        if not probes["has_nav"] and not any(kw in h for kw in ("contact","book","signup","get started","buy","order","call","quote")):
            issues.append({"code": "poor_navigation", "msg": "Navigation or CTA missing", "suggestion": "Add clear menu and primary CTA."})
        return max(0.0, min(10.0, s))

    def score_seo():
        s = 2.0
        s += 2.0 * probes["has_title"]
        s += 2.0 * probes["has_meta_description"]
        s += 2.0 * probes["has_h1"]
        # alt coverage
        if features["imgs"] > 0:
            alt_rate = min(1.0, features["alts"] / float(features["imgs"]))
            s += 2.0 * alt_rate
            if alt_rate < 0.5:
                issues.append({"code": "missing_alt", "msg": "Many images missing alt attributes", "suggestion": "Add descriptive alt text to images."})
        return max(0.0, min(10.0, s))

    def score_branding():
        s = 4.0
        if probes["has_logo"]:
            s += 3.0
        else:
            issues.append({"code": "no_logo", "msg": "No obvious logo or brand element found", "suggestion": "Add a clear logo and brand mark."})
        return max(0.0, min(10.0, s))

    def score_content():
        s = 4.0
        if probes["has_services"]:
            s += 3.0
        # detect placeholder text
        if "lorem ipsum" in h:
            s -= 2.0
            issues.append({"code": "placeholder_text", "msg": "Placeholder or lorem ipsum detected", "suggestion": "Replace placeholder text with real content."})
        return max(0.0, min(10.0, s))

    def score_security():
        s = 5.0
        # mixed content: presence of http:// resources on https pages
        if "http://" in h and "https://" in h:
            s -= 3.0
            issues.append({"code": "mixed_content", "msg": "Mixed HTTP/HTTPS resources detected", "suggestion": "Serve all resources over HTTPS."})
        # prefer https links present
        if "https://" in h:
            s += 2.0
        return max(0.0, min(10.0, s))

    def score_accessibility():
        s = 4.0
        # low contrast heuristic
        if contrast < 20.0:
            s -= 2.0
            issues.append({"code": "low_contrast", "msg": "Low visual contrast detected", "suggestion": "Increase contrast for text and UI elements."})
        # missing alt text counted in SEO also affects accessibility
        if features["imgs"] > 0 and features["alts"] < features["imgs"]:
            s -= 1.0
        return max(0.0, min(10.0, s))

    def score_technical():
        s = 5.0
        if not probes["has_favicon"]:
            s -= 1.0
            issues.append({"code": "missing_favicon", "msg": "No favicon detected", "suggestion": "Add a favicon for branding and polish."})
        # deprecated tags
        if "<font" in h or "<center" in h:
            s -= 2.0
            issues.append({"code": "deprecated_html", "msg": "Deprecated HTML tags detected", "suggestion": "Update HTML to modern semantic elements."})
        return max(0.0, min(10.0, s))

    # --- New category scoring functions ---
    def score_conversion_optimization():
        s = 5.0
        # presence of form or CTA keywords
        if probes["has_form"]:
            s += 2.0
        if any(kw in h for kw in ("contact","book","signup","get started","buy","order","call","quote")):
            s += 2.0
        # penalize low contrast (CTA visibility)
        if contrast < 30.0:
            s -= 1.5
            issues.append({"code": "poor_cta_contrast", "msg": "Low contrast around CTAs or hero text", "suggestion": "Increase CTA contrast and prominence."})
        # penalize missing CTA and no forms
        if not probes["has_form"] and not any(kw in h for kw in ("contact","signup","book","get started")):
            s -= 2.0
            issues.append({"code": "no_cta", "msg": "No clear CTA or contact form detected", "suggestion": "Add a prominent CTA above the fold."})
        return max(0.0, min(10.0, s))

    def score_local_seo():
        s = 3.0
        # presence of address/phone or Google Maps iframe hints
        if any(x in h for x in ("address","phone","tel:","map.google")):
            s += 3.0
        # presence of LocalBusiness schema
        if "localbusiness" in h or "@type\": \"LocalBusiness\"" in h:
            s += 2.0
        # penalize missing basic meta/title for SEO
        if not probes["has_meta_description"] or not probes["has_title"]:
            s -= 1.0
            issues.append({"code": "missing_local_info", "msg": "Local info or meta tags missing", "suggestion": "Add NAP and LocalBusiness schema for local SEO."})
        return max(0.0, min(10.0, s))

    def score_ux():
        s = 5.0
        # responsiveness helps UX
        s += 2.0 * responsiveness
        # penalize cluttered layout (very high edge density may indicate busy UI)
        if edge_density > 0.4:
            s -= 1.0
            issues.append({"code": "cluttered_layout", "msg": "Layout appears visually cluttered", "suggestion": "Increase whitespace and simplify layout."})
        # readability/contrast
        if contrast < 25.0:
            s -= 1.0
            issues.append({"code": "poor_readability", "msg": "Low contrast may hurt readability", "suggestion": "Increase text contrast and font sizes."})
        return max(0.0, min(10.0, s))

    def score_technical_seo():
        s = 4.0
        if probes["has_title"]:
            s += 2.0
        if probes["has_meta_description"]:
            s += 1.0
        if probes["has_h1"]:
            s += 1.0
        # alt coverage
        if features["imgs"] > 0:
            alt_rate = min(1.0, features["alts"] / float(features["imgs"]))
            s += 2.0 * alt_rate
            if alt_rate < 0.5:
                issues.append({"code": "missing_alt", "msg": f"Many images missing alt attributes (alt rate {round(alt_rate*100)}%)", "suggestion": "Add descriptive alt text to images."})
        return max(0.0, min(10.0, s))

    def score_compliance_privacy():
        s = 5.0
        # look for privacy policy or cookie banner keywords
        if any(x in h for x in ("privacy policy","cookie","gdpr","ccpa")):
            s += 2.0
        else:
            issues.append({"code": "no_privacy_policy", "msg": "No obvious privacy policy or cookie consent found", "suggestion": "Add a privacy policy and consent banner if collecting user data."})
        # mixed content reduces compliance/trust
        if "http://" in h and "https://" in h:
            issues.append({"code": "mixed_content", "msg": "Mixed HTTP/HTTPS resources detected", "suggestion": "Serve all resources over HTTPS."})
            s -= 2.0
        return max(0.0, min(10.0, s))

    def score_mobile_interaction():
        s = 5.0
        s += 3.0 * probes["has_viewport"]
        # penalize heavy mobile images
        if features["screenshot_mobile_kb"] > 300:
            s -= 1.5
            issues.append({"code": "mobile_heavy_images", "msg": "Large mobile images", "suggestion": "Optimize mobile images and use responsive srcset."})
        # penalize if mobile appears blank/different
        if edge_density < 0.02:
            s -= 2.0
            issues.append({"code": "mobile_blank", "msg": "Mobile/desktop screenshots differ or page blank on mobile", "suggestion": "Ensure responsive layout and wait for content before screenshot."})
        return max(0.0, min(10.0, s))

    def score_content_structure():
        s = 4.0
        s += 2.0 * probes["has_h1"]
        if probes["has_services"]:
            s += 1.0
        if len(h) < 300:
            s -= 2.0
            issues.append({"code": "thin_content", "msg": "Content appears thin", "suggestion": "Add more descriptive service and about content."})
        if "lorem ipsum" in h:
            issues.append({"code": "placeholder_text", "msg": "Placeholder or lorem ipsum detected", "suggestion": "Replace placeholder text with real content."})
        return max(0.0, min(10.0, s))

    def score_visual_identity():
        s = 4.0
        if probes["has_logo"]:
            s += 3.0
        if probes["has_favicon"]:
            s += 1.0
        # low colorfulness indicates weak palette
        if colorfulness < 8.0:
            s -= 1.0
            issues.append({"code": "weak_branding", "msg": "Low colorfulness / weak branding", "suggestion": "Adopt a stronger, consistent color palette and brand assets."})
        return max(0.0, min(10.0, s))

    def score_performance_infra():
        s = 5.0
        if scripts > 10:
            s -= 2.0
            issues.append({"code": "many_scripts", "msg": "Many external scripts detected", "suggestion": "Audit and defer non-critical scripts."})
        if features["screenshot_desktop_kb"] > 500:
            s -= 2.0
            issues.append({"code": "large_images", "msg": "Large images detected", "suggestion": "Compress hero images and use modern formats."})
        return max(0.0, min(10.0, s))

    def score_tech_modernity():
        s = 5.0
        # presence of google fonts or modern markers
        if "fonts.googleapis.com" in h or "<script type=\"module\"" in h:
            s += 2.0
        if "<font" in h or "<center" in h:
            s -= 2.0
            issues.append({"code": "deprecated_html", "msg": "Deprecated HTML tags detected", "suggestion": "Update HTML to modern semantic elements."})
        # detect hints of SPA frameworks
        if any(x in h for x in ("react-dom","vue","ng-app","svelte")):
            s += 1.0
        return max(0.0, min(10.0, s))

    # compute each category
    categories = {
        "mobile_responsiveness": round(score_mobile(), 1),
        "modern_design": round(score_modern_design(), 1),
        "page_speed": round(score_page_speed(), 1),
        "navigation": round(score_navigation(), 1),
        "seo": round(score_seo(), 1),
        "branding": round(score_branding(), 1),
        "content": round(score_content(), 1),
        "security": round(score_security(), 1),
        "accessibility": round(score_accessibility(), 1),
        "technical": round(score_technical(), 1),
        # new categories
        "conversion_optimization": round(score_conversion_optimization(), 1),
        "local_seo": round(score_local_seo(), 1),
        "ux": round(score_ux(), 1),
        "technical_seo": round(score_technical_seo(), 1),
        "compliance_privacy": round(score_compliance_privacy(), 1),
        "mobile_interaction": round(score_mobile_interaction(), 1),
        "content_structure": round(score_content_structure(), 1),
        "visual_identity": round(score_visual_identity(), 1),
        "performance_infra": round(score_performance_infra(), 1),
        "tech_modernity": round(score_tech_modernity(), 1),
    }

    # default weights (sum should be 1.0)
    # Adjusted weights including new categories (sum ~= 1.0)
    weights = {
        "mobile_responsiveness": 0.12,
        "modern_design": 0.10,
        "page_speed": 0.10,
        "navigation": 0.07,
        "seo": 0.06,
        "branding": 0.05,
        "content": 0.06,
        "security": 0.05,
        "accessibility": 0.05,
        "technical": 0.05,
        "conversion_optimization": 0.08,
        "local_seo": 0.03,
        "ux": 0.05,
        "technical_seo": 0.03,
        "compliance_privacy": 0.02,
        "mobile_interaction": 0.03,
        "content_structure": 0.03,
        "visual_identity": 0.03,
        "performance_infra": 0.03,
        "tech_modernity": 0.03,
    }

    # weighted average (categories are 0..10)
    weighted = 0.0
    for k, v in categories.items():
        weighted += (v / 10.0) * weights.get(k, 0.0)
    overall = int(max(1, min(10, round(weighted * 9.0 + 1))))

    # Build explanation and suggestion deterministically
    if issues:
        # Deduplicate issues by code to avoid repeated messages when multiple
        # category scorers append the same issue (e.g., many_scripts or mixed_content).
        seen = {}
        unique_issues = []
        for it in issues:
            code = it.get('code') or it.get('msg')
            if code not in seen:
                seen[code] = True
                unique_issues.append(it)
        explanation = f"Detected {len(unique_issues)} issue(s): {', '.join(i['msg'] for i in unique_issues)}."
        # Prioritized selection: prefer certain issue types to form the top suggestion
        priority_order = ['no_cta', 'many_scripts', 'missing_alt', 'large_images', 'mobile_heavy_images', 'mobile_blank']
        top_issue = None
        for code in priority_order:
            for it in unique_issues:
                if it.get('code') == code:
                    top_issue = it
                    break
            if top_issue:
                break
        if not top_issue:
            # fallback to impact-based choice if none of the prioritized codes present
            impacts = []
            for it in unique_issues:
                code = it.get('code', '')
                impact = 1
                try:
                    if code == 'many_scripts':
                        impact = int(scripts or 0)
                    elif code == 'large_images':
                        impact = int(features.get('screenshot_desktop_kb', 0))
                    elif code == 'mobile_heavy_images':
                        impact = int(features.get('screenshot_mobile_kb', 0))
                    elif code == 'missing_alt':
                        imgs = int(features.get('imgs', 0))
                        alts = int(features.get('alts', 0))
                        impact = max(0, imgs - alts)
                    elif code == 'mobile_blank':
                        impact = 100
                    else:
                        impact = 1
                except Exception:
                    impact = 1
                impacts.append((impact, it))
            impacts.sort(key=lambda x: x[0], reverse=True)
            top_issue = impacts[0][1] if impacts else None
        suggestion = top_issue.get('suggestion') if top_issue and top_issue.get('suggestion') else (unique_issues[0].get('suggestion') if unique_issues else "Consider continuous monitoring and performance improvements.")
        # Replace issues list with deduplicated list for downstream storage
        issues = unique_issues
    else:
        explanation = "No major deterministic issues detected."
        suggestion = "Consider continuous monitoring and performance improvements."

    reason = f"Deterministic score {overall}. Category breakdown: " + ", ".join(f"{k}:{v}" for k, v in categories.items())

    return {
        "score": overall,
        "reason": reason,
        "explanation": explanation,
        "suggestion": suggestion,
        "features": features,
        "issues": issues,
        "categories": categories,
    }

    # Build a minimal JSON payload of numeric features to send to OpenAI (minimize tokens)
    feature_bullets = {
        "score": score,
        "responsiveness": features_summary["responsiveness"],
        "brightness": features_summary["brightness"],
        "contrast": features_summary["contrast"],
        "colorfulness": features_summary["colorfulness"],
        "edge_density": features_summary["edge_density"],
        "html_length": len(html),
    }

    system_msg = (
        "You are a very concise assistant. Given numeric metrics, return ONLY a single-line JSON object with two fields:"
        " {\"explanation\":\"<one short sentence>\",\"suggestion\":\"<one very short sentence>\"}."
        " No extra text, no markdown, no newlines. Keep each field under ~80 characters."
    )

    user_msg = json.dumps(feature_bullets)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8),
           retry=retry_if_exception_type(Exception))
    def _call_openai(system_msg: str, user_msg: str) -> str:
        # Respect rate limit before calling OpenAI
        try:
            openai_rate_limiter.acquire()
        except Exception:
            # If rate limiter fails, log and continue (best-effort)
            logger.exception("Rate limiter acquire failed")
        # Primary: use direct HTTP REST call to OpenAI Chat Completions to avoid client-version mismatches.
        api_key = OPENAI_API_KEY
        if not api_key:
            raise RuntimeError("No OPENAI_API_KEY configured")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 180,
            "temperature": 0.0,
        }
        try:
            # Aggressively reduce max tokens to minimize cost
            payload["max_tokens"] = 20
            resp = httpx.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=20.0)
            resp.raise_for_status()
            j = resp.json()
            if "choices" in j and len(j["choices"]) > 0:
                content = j["choices"][0].get("message", {}).get("content") or j["choices"][0].get("text")
                if content:
                    return content.strip()
            raise RuntimeError(f"Unexpected REST response shape: {j}")
        except Exception as http_e:
            logger.exception("OpenAI HTTP REST call failed: %s", http_e)
            # Fallback: attempt to use the installed `openai` module ChatCompletion API as a last resort.
            try:
                resp = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
                    max_tokens=20,
                    temperature=0.0,
                )
                return resp["choices"][0]["message"]["content"].strip()
            except Exception as legacy_e:
                logger.exception("Legacy openai.ChatCompletion fallback failed: %s", legacy_e)
                # Propagate original HTTP exception for retry wrapper
                raise http_e

    try:
        content = _call_openai(system_msg, user_msg)
        # Try to parse compact JSON response to extract explanation and suggestion
        explanation = None
        suggestion = None
        try:
            j = json.loads(content)
            explanation = j.get("explanation") or j.get("explain") or j.get("ex")
            suggestion = j.get("suggestion") or j.get("advice") or j.get("suggest")
        except Exception:
            # leave as None
            pass
        # Build fallback reason string
        if explanation and suggestion:
            reason = f"{explanation.strip()} Suggestion: {suggestion.strip()}"
        else:
            reason = content.strip()
        return {"score": score, "reason": reason, "explanation": explanation, "suggestion": suggestion}
    except Exception as e:
        logger.exception("OpenAI call failed: %s", e)
        # Also log full traceback string to ensure it appears in stdout/stderr
        tb = traceback.format_exc()
        logger.error("OpenAI call traceback:\n%s", tb)
        reason = (
            f"Heuristic score {score}. (OpenAI call failed: {e}). Features: responsiveness={features_summary['responsiveness']}, "
            f"brightness={features_summary['brightness']}, contrast={features_summary['contrast']}, "
            f"colorfulness={features_summary['colorfulness']}, edge_density={features_summary['edge_density']}.")
        return {"score": score, "reason": reason, "explanation": reason, "suggestion": ""}
