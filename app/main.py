from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from .db import SessionLocal, init_db
from . import scraper, models
# analyzer depends on numpy which can fail to import on some dev machines
# Import it lazily and fall back to None so the server can still start.
analyzer = None
try:
    from . import analyzer as analyzer
except Exception:
    import logging as _logging
    _logging.getLogger("app.main").warning("Failed to import analyzer (numpy may be missing). Analyzer endpoints will return an error until resolved.", exc_info=True)
from .logging_config import get_logger
import os
from datetime import datetime, timedelta

logger = get_logger("app.main")

# TTL to avoid re-scanning the same URL (seconds). Default 24h.
SCAN_CACHE_TTL = int(os.getenv("SCAN_CACHE_TTL_SECONDS", str(24 * 3600)))
from .schemas import ScanRequest, LeadResponse, BatchScanRequest, BatchScanResponse
import csv
import io
import shutil
import subprocess
from .utils import timestamp_str, ensure_data_dirs
import json
import httpx
import tempfile

app = FastAPI(title="AltruDigiTech LeadGen")
init_db()

# Serve static dashboard and screenshots
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/data", StaticFiles(directory="data"), name="data")


@app.get("/dashboard")
def dashboard():
    return RedirectResponse(url="/static/index.html")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def persist_lead(db: Session, website_url: str, business_name: str, screenshot_desktop: str, screenshot_mobile: str, score: int, reason: str, explanation: str = None, suggestion: str = None, features_json: str = None):
    # Ensure website_url is a plain string (Pydantic HttpUrl may be passed)
    website_url = str(website_url) if website_url is not None else None

    lead = models.Lead(
        website_url=website_url,
        business_name=business_name,
        screenshot_desktop=screenshot_desktop,
        screenshot_mobile=screenshot_mobile,
        score=score,
        reason=reason,
        explanation=explanation,
        suggestion=suggestion,
        features_json=features_json,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead

def process_and_store_sync(db: Session, website_url: str, business_name: str):
    # Normalize URL to string to avoid passing Pydantic HttpUrl into SQLAlchemy
    website_url = str(website_url) if website_url is not None else None
    # Deduplication: if a lead for this URL exists and is recent, return it
    existing = db.query(models.Lead).filter(models.Lead.website_url == website_url).order_by(models.Lead.created_at.desc()).first()
    if existing:
        age = datetime.utcnow() - existing.created_at
        if age.total_seconds() <= SCAN_CACHE_TTL:
            logger.info("Returning cached lead for %s (age %s seconds)", website_url, int(age.total_seconds()))
            return existing

    # Use ephemeral screenshots (in-memory -> temp files) to avoid growing data/screenshots
    desktop_tmp = None
    mobile_tmp = None
    try:
        dscr = scraper.screenshot_url_bytes(website_url, device='desktop')
        mscr = scraper.screenshot_url_bytes(website_url, device='mobile')
        desktop_bytes = dscr.get('screenshot_bytes')
        mobile_bytes = mscr.get('screenshot_bytes')
        page_html = dscr.get('html') or mscr.get('html') or ''
        # write to temporary files for analyzer (PIL expects a filesystem path)
        desktop_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_desktop.png')
        desktop_tmp.write(desktop_bytes)
        desktop_tmp.flush()
        desktop_tmp.close()
        mobile_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_mobile.png')
        mobile_tmp.write(mobile_bytes)
        mobile_tmp.flush()
        mobile_tmp.close()
        analysis = analyzer.analyze_images(desktop_tmp.name, mobile_tmp.name, html=page_html)
    finally:
        # cleanup temp files after analysis
        try:
            if desktop_tmp and os.path.exists(desktop_tmp.name):
                os.unlink(desktop_tmp.name)
        except Exception:
            pass
        try:
            if mobile_tmp and os.path.exists(mobile_tmp.name):
                os.unlink(mobile_tmp.name)
        except Exception:
            pass
    # If analyzer fell back (OpenAI failure), log a warning with context so it appears in server logs
    if analysis and (analysis.get("explanation") is None or analysis.get("suggestion") is None):
        logger.warning("Analyzer returned heuristic/fallback reason for %s: %s", website_url, analysis.get("reason"))
    # Persist both numeric features and category breakdown together in `features_json` as a small JSON blob
    analysis_blob = {
        "features": analysis.get("features") or {},
        "categories": analysis.get("categories") or {},
    }
    # Do not store persistent screenshot paths to avoid repo size growth; images are ephemeral
    lead = persist_lead(
        db,
        website_url,
        business_name,
        '',
        '',
        analysis.get("score"),
        analysis.get("reason"),
        explanation=analysis.get("explanation"),
        suggestion=analysis.get("suggestion"),
        features_json=json.dumps(analysis_blob),
    )
    return lead


def process_and_store_background(website_url: str, business_name: str):
    # This function creates its own DB session so it can run in background tasks
    db = SessionLocal()
    try:
        website_url = str(website_url) if website_url is not None else None
        # Use ephemeral screenshots for background processing as well
        desktop_tmp = None
        mobile_tmp = None
        try:
            dscr = scraper.screenshot_url_bytes(website_url, device='desktop')
            mscr = scraper.screenshot_url_bytes(website_url, device='mobile')
            desktop_bytes = dscr.get('screenshot_bytes')
            mobile_bytes = mscr.get('screenshot_bytes')
            page_html = dscr.get('html') or mscr.get('html') or ''
            desktop_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_desktop.png')
            desktop_tmp.write(desktop_bytes)
            desktop_tmp.flush()
            desktop_tmp.close()
            mobile_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_mobile.png')
            mobile_tmp.write(mobile_bytes)
            mobile_tmp.flush()
            mobile_tmp.close()
            analysis = analyzer.analyze_images(desktop_tmp.name, mobile_tmp.name, html=page_html)
        finally:
            try:
                if desktop_tmp and os.path.exists(desktop_tmp.name):
                    os.unlink(desktop_tmp.name)
            except Exception:
                pass
            try:
                if mobile_tmp and os.path.exists(mobile_tmp.name):
                    os.unlink(mobile_tmp.name)
            except Exception:
                pass
        analysis_blob = {
            "features": analysis.get("features") or {},
            "categories": analysis.get("categories") or {},
        }
        persist_lead(
            db,
            website_url,
            business_name,
            '',
            '',
            analysis.get("score"),
            analysis.get("reason"),
            explanation=analysis.get("explanation"),
            suggestion=analysis.get("suggestion"),
            features_json=json.dumps(analysis_blob),
        ) 
    except Exception as e:
        # background job should not raise to the server; log the exception for inspection
        import logging
        logging.getLogger("app.background").exception("Background job failed for %s: %s", website_url, e)
    finally:
        db.close()

@app.post("/scan-url", response_model=LeadResponse)
def scan_url(req: ScanRequest, background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    # For MVP we run synchronously. To offload, use background_tasks.add_task(process_and_store_background, req.website_url, req.business_name)
    try:
        lead = process_and_store_sync(db, req.website_url, req.business_name)
        return lead
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/batch-scan", response_model=BatchScanResponse)
def batch_scan(req: BatchScanRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # Enqueue background tasks for each URL and return accepted count
    accepted = 0
    for url in req.website_urls:
        background_tasks.add_task(process_and_store_background, str(url), req.business_name)
        accepted += 1
    return {"accepted": accepted}

@app.get("/leads")
def list_leads(db: Session = Depends(get_db)):
    leads = db.query(models.Lead).order_by(models.Lead.score.desc().nullslast()).all()
    return leads

@app.get("/leads/{lead_id}", response_model=LeadResponse)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get('/leads/{lead_id}/screenshot')
def get_lead_screenshot(lead_id: int, device: str = 'desktop', db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail='Lead not found')
    try:
        # Generate ephemeral screenshot bytes (no file written)
        data = scraper.screenshot_url_bytes(str(lead.website_url), device=device)
        img_bytes = data.get('screenshot_bytes')
        if not img_bytes:
            raise HTTPException(status_code=500, detail='Failed to capture screenshot')
        return StreamingResponse(io.BytesIO(img_bytes), media_type='image/png')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/leads/{lead_id}/reanalysis", response_model=LeadResponse)
def reanalyze_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    try:
        # Attempt to fetch current page HTML to provide richer probes for analysis
        # Use ephemeral screenshots for reanalysis instead of stored files
        desktop_tmp = None
        mobile_tmp = None
        try:
            dscr = scraper.screenshot_url_bytes(str(lead.website_url), device='desktop')
            mscr = scraper.screenshot_url_bytes(str(lead.website_url), device='mobile')
            desktop_bytes = dscr.get('screenshot_bytes')
            mobile_bytes = mscr.get('screenshot_bytes')
            page_html = dscr.get('html') or mscr.get('html') or ''
            desktop_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_desktop.png')
            desktop_tmp.write(desktop_bytes)
            desktop_tmp.flush()
            desktop_tmp.close()
            mobile_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_mobile.png')
            mobile_tmp.write(mobile_bytes)
            mobile_tmp.flush()
            mobile_tmp.close()
            analysis = analyzer.analyze_images(desktop_tmp.name, mobile_tmp.name, html=page_html)
        finally:
            try:
                if desktop_tmp and os.path.exists(desktop_tmp.name):
                    os.unlink(desktop_tmp.name)
            except Exception:
                pass
            try:
                if mobile_tmp and os.path.exists(mobile_tmp.name):
                    os.unlink(mobile_tmp.name)
            except Exception:
                pass
        lead.score = analysis.get("score")
        lead.reason = analysis.get("reason")
        lead.explanation = analysis.get("explanation")
        lead.suggestion = analysis.get("suggestion")
        analysis_blob = {
            "features": analysis.get("features") or {},
            "categories": analysis.get("categories") or {},
        }
        lead.features_json = json.dumps(analysis_blob)
        db.commit()
        db.refresh(lead)
        return lead
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run_lighthouse_bg(lead_id: int, url: str):
    # background worker to run lighthouse CLI if available
    ensure_data_dirs()
    ts = timestamp_str()
    out_path = f"data/audits/{lead_id}_{ts}_lighthouse.json"
    lw = shutil.which("lighthouse")
    if not lw:
        # try npx if Node is available
        npx = shutil.which("npx")
        if not npx:
            # write error file
            with open(out_path, "w") as f:
                json.dump({"error": "lighthouse CLI not found. Install Node.js and run `npm i -g lighthouse` or ensure `npx` is available."}, f)
            logger.error("lighthouse CLI not found; cannot run audit for %s", url)
            return
            # verify node version is recent enough; lighthouse v13+ requires Node >=22.19
            node = shutil.which("node")
            if node:
                try:
                    vproc = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
                    ver = vproc.stdout.strip().lstrip('v')
                    parts = ver.split('.')
                    major = int(parts[0]) if parts and parts[0].isdigit() else 0
                    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                    if not (major > 22 or (major == 22 and minor >= 19) or major >= 23):
                        with open(out_path, "w") as f:
                            json.dump({"error": f"Node.js {ver} detected. Lighthouse requires Node >=22.19 (or use a compatible lighthouse version). Please upgrade Node for audits."}, f)
                        logger.error("Node.js version %s is too old for lighthouse; cannot run audit for %s", ver, url)
                        return
                except Exception:
                    # if we cannot determine node version, continue and let npx fail with its own message
                    pass
            # use npx to run lighthouse
            cmd = [npx, "lighthouse", url, "--output=json", f"--output-path={out_path}", "--quiet"]
    else:
        cmd = [lw, url, "--output=json", f"--output-path={out_path}", "--quiet"]
    try:
        proc = subprocess.run(cmd, timeout=180, capture_output=True)
        if proc.returncode != 0:
            logger.error("Lighthouse failed for %s: %s", url, proc.stderr.decode(errors='ignore'))
            with open(out_path, "w") as f:
                json.dump({"error": "lighthouse failed", "stderr": proc.stderr.decode(errors='ignore')}, f)
            # update DB with failure
            try:
                db = SessionLocal()
                lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
                if lead:
                    lead.last_audit_path = out_path
                    lead.audit_status = 'failed'
                    from datetime import datetime
                    lead.audit_completed_at = datetime.utcnow()
                    db.commit()
            except Exception:
                logger.exception("Failed to update lead audit record after lighthouse failure for %s", url)
            finally:
                try:
                    db.close()
                except Exception:
                    pass
    except Exception as e:
        logger.exception("Exception running lighthouse for %s: %s", url, e)
        with open(out_path, "w") as f:
            json.dump({"error": str(e)}, f)
        # update DB with failure
        try:
            db = SessionLocal()
            lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
            if lead:
                lead.last_audit_path = out_path
                lead.audit_status = 'failed'
                from datetime import datetime
                lead.audit_completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            logger.exception("Failed to update lead audit record after lighthouse exception for %s", url)
        finally:
            try:
                db.close()
            except Exception:
                pass
    else:
        # success - ensure file exists and update DB
        try:
            db = SessionLocal()
            lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
            if lead:
                lead.last_audit_path = out_path
                lead.audit_status = 'completed'
                from datetime import datetime
                lead.audit_completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            logger.exception("Failed to update lead audit record after lighthouse success for %s", url)
        finally:
            try:
                db.close()
            except Exception:
                pass


@app.post("/leads/{lead_id}/lighthouse")
def run_lighthouse(lead_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    # Schedule background lighthouse run and return accepted
    background_tasks.add_task(_run_lighthouse_bg, lead_id, str(lead.website_url))
    return {"status": "accepted", "lead_id": lead_id}


@app.get('/leads/{lead_id}/audit/latest')
def get_latest_audit(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail='Lead not found')
    if not lead.last_audit_path:
        raise HTTPException(status_code=404, detail='No audit found for this lead')
    path = lead.last_audit_path
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='Audit file not found on disk')
    def iterfile():
        with open(path, 'rb') as f:
            yield from f
    return StreamingResponse(iterfile(), media_type='application/json')

@app.post("/leads/{lead_id}/contact")
def mark_contacted(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.contacted = True
    db.commit()
    return {"status": "ok", "lead_id": lead_id}


@app.post("/leads/{lead_id}/generate-email")
def generate_email(lead_id: int, use_ai: bool = False, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    # load categories/features
    cats = {}
    try:
        fj = json.loads(lead.features_json) if lead.features_json else {}
        cats = fj.get("categories", {}) if isinstance(fj, dict) else {}
    except Exception:
        cats = {}

    # parse short issue list from explanation (if present)
    issues = []
    if lead.explanation:
        parts = lead.explanation.split(":", 1)
        if len(parts) > 1:
            issues = [p.strip() for p in parts[1].split(",") if p.strip()]

    # friendly recommendation mapping: (title, blurb, lift, effort, lift_score)
    rec_map = {
        "large_images": ("Optimize large images", "Compress images and serve modern formats (WebP/AVIF).", "Medium", "1–4 hours", 2),
        "large_mobile_images": ("Serve smaller mobile images", "Use responsive srcset and smaller mobile images to reduce mobile payload.", "Medium", "1–4 hours", 2),
        "many_external_scripts": ("Defer non‑critical scripts", "Audit third‑party widgets and defer or lazy‑load non‑essential scripts.", "High", "1–2 days", 3),
        "mixed_http_https": ("Fix mixed content", "Serve all resources over HTTPS to avoid browser warnings.", "Low", "<1 hour", 1),
        "missing_alt": ("Add descriptive alt text", "Provide alt attributes for important images to help accessibility and SEO.", "Low", "30–90 minutes", 1),
        "no_cta": ("Add a clear primary CTA", "Make the main call‑to‑action prominent in the header/hero to increase conversions.", "High", "1–4 hours", 3),
        "thin_content": ("Replace placeholder content", "Add concise service descriptions and contact info to build trust.", "Medium", "1–2 days", 2),
        "low_contrast": ("Improve contrast", "Increase color contrast for buttons/links to improve readability.", "Low", "1–3 hours", 1),
        "many_images": ("Lazy‑load below‑the‑fold images", "Prioritize above‑the‑fold images and lazy‑load the rest.", "Medium", "2–8 hours", 2),
        "performance_infra": ("Consider CDN/hosting improvements", "Use a CDN or better hosting for large assets to reduce load times.", "High", "1–3 days", 3),
        "seo_basics": ("Add SEO basics", "Ensure title/meta descriptions and descriptive image alt text are present.", "Medium", "1–3 hours", 2),
        "accessibility": ("Accessibility improvements", "Add ARIA labels, keyboard focus, and alt text for key images.", "Low", "2–8 hours", 1),
        "security_headers": ("Add security headers", "Configure HSTS and other secure headers to improve trust.", "Low", "<1 hour", 1),
    }

    def detect_issue_code(text: str):
        t = (text or "").lower()
        if "mobile" in t and "image" in t:
            return "large_mobile_images"
        if "large" in t and "image" in t:
            return "large_images"
        if "external" in t or "scripts" in t:
            return "many_external_scripts"
        if "mixed" in t or "http/https" in t or "http" in t and "https" in t:
            return "mixed_http_https"
        if "alt" in t:
            return "missing_alt"
        if "cta" in t or "call to action" in t or "navigation" in t:
            return "no_cta"
        if "placeholder" in t or "lorem" in t or "thin" in t:
            return "thin_content"
        if "contrast" in t:
            return "low_contrast"
        if "images" in t and "large" not in t:
            return "many_images"
        if "cdn" in t or "hosting" in t or "server" in t or "infrastructure" in t:
            return "performance_infra"
        if "seo" in t or "meta" in t or "title" in t:
            return "seo_basics"
        if "accessibility" in t or "aria" in t:
            return "accessibility"
        if "mixed" in t or "https" in t:
            return "security_headers"
        return None

    detected_codes = []
    for it in issues:
        code = detect_issue_code(it)
        if code and code not in detected_codes:
            detected_codes.append(code)

    # deterministic selection: prefer conversion/UX issues first
    priority_codes = ["no_cta", "navigation", "modern_design"]
    selected_recs = []
    for p in priority_codes:
        if p in detected_codes and p in rec_map:
            selected_recs.append((p,) + rec_map[p])
            break

    # then pick highest lift among detected codes
    remaining = [c for c in detected_codes if c not in [r[0] for r in selected_recs]]
    remaining_with_score = []
    for c in remaining:
        if c in rec_map:
            remaining_with_score.append((rec_map[c][4], c))
    remaining_with_score.sort(reverse=True)
    for _, c in remaining_with_score:
        selected_recs.append((c,) + rec_map[c])
        if len(selected_recs) >= 2:
            break

    # fallback: use lowest scoring categories if needed
    if len(selected_recs) < 2:
        cat_items = []
        try:
            cat_items = sorted([(k, float(v)) for k, v in (cats.items() if isinstance(cats, dict) else [])], key=lambda x: x[1])
        except Exception:
            cat_items = list(cats.items()) if isinstance(cats, dict) else []
        for k, v in cat_items[:3]:
            label, suggestion = (k.replace("_", " ").capitalize(), lead.suggestion or "Consider improvements")
            selected_recs.append((k, label, suggestion, "Medium", "1–3 hours", 2))
            if len(selected_recs) >= 2:
                break

    # build recommendation bullets
    rec_bullets = []
    for r in selected_recs[:2]:
        # r format: (code, title, blurb, lift, effort, [score])
        code = r[0]
        title = r[1]
        blurb = r[2]
        lift = r[3]
        effort = r[4]
        rec_bullets.append(f"{title} — {blurb} (Lift: {lift}. Effort: {effort})")

    # evidence lines from explicit issues
    evidence = issues[:2]

    # expanded suggestions by category depending on score (lower score -> stronger suggestion)
    suggestions_by_category = {
        "page_speed": [
            (3, "Immediate: Compress and serve images in modern formats, enable caching headers and remove blocking scripts."),
            (6, "Recommended: Add image optimization pipeline, use lazy loading, and audit third‑party scripts."),
            (10, "Optional: Monitor performance metrics and run targeted audits to maintain speed.")
        ],
        "mobile_responsiveness": [
            (3, "Immediate: Fix responsive breakpoints in the hero and navigation so content doesn't overflow on small screens."),
            (6, "Recommended: Provide smaller image variants and check touch target sizes."),
            (10, "Optional: Run usability checks on common mobile devices.")
        ],
        "conversion_optimization": [
            (3, "Immediate: Add a clear, prominent primary CTA in the header/hero and make forms easier to find."),
            (6, "Recommended: A/B test CTA wording and placement to improve clicks."),
            (10, "Optional: Add conversion tracking to measure impact.")
        ],
        "seo": [
            (3, "Immediate: Add unique title tags and meta descriptions on key pages and fix broken links."),
            (6, "Recommended: Add descriptive alt text and structured data for important content."),
            (10, "Optional: Create a simple sitemap and submit to search engines.")
        ],
        "accessibility": [
            (3, "Immediate: Add missing alt attributes on meaningful images and ensure focusable elements are keyboard accessible."),
            (6, "Recommended: Increase color contrast for buttons and test with a screen reader."),
            (10, "Optional: Add ARIA labels for complex widgets and run an accessibility audit.")
        ],
        "visual_identity": [
            (3, "Immediate: Clarify logo and brand elements in the header and add a favicon."),
            (6, "Recommended: Improve typography hierarchy and hero spacing."),
            (10, "Optional: Polish iconography and color accents for consistency.")
        ],
        "performance_infra": [
            (3, "Immediate: Move large static assets to a CDN and enable compression on the server."),
            (6, "Recommended: Inspect hosting plan and consider edge caching."),
            (10, "Optional: Schedule an infrastructure review for peak traffic handling.")
        ],
    }

    # pick top 2 lowest scoring categories to surface actionable suggestions
    cat_items = []
    try:
        cat_items = sorted([(k, float(v)) for k, v in (cats.items() if isinstance(cats, dict) else [])], key=lambda x: x[1])
    except Exception:
        cat_items = list(cats.items()) if isinstance(cats, dict) else []
    top_cats = [c for c, _ in cat_items[:2]]

    def pick_suggestion_for_category(cat_key: str, score_val: float):
        if cat_key not in suggestions_by_category:
            return None
        for threshold, text in suggestions_by_category[cat_key]:
            if score_val <= threshold:
                return text
        return suggestions_by_category[cat_key][-1][1]

    category_suggestions = []
    for c in top_cats:
        try:
            s_val = float(cats.get(c, 10))
        except Exception:
            s_val = 10.0
        picked = pick_suggestion_for_category(c, s_val)
        if picked:
            category_suggestions.append((c, s_val, picked))

    subject = f"Quick wins to improve {lead.business_name or 'your website'} ({lead.score or '—'}/10)"
    preview = f"Small, high‑impact fixes I found on {lead.website_url} — quick wins available."
    body_lines = []
    body_lines.append("Hi {{business_name}},")
    body_lines.append("")
    body_lines.append(f"I reviewed {{website}} and scored it {lead.score or '—'}/10. Below are friendly, non‑critical recommendations to improve performance, usability, and conversions:")
    body_lines.append("")
    for b in rec_bullets:
        body_lines.append(f"- {b}")
    if evidence:
        body_lines.append("")
        body_lines.append("Evidence:")
        for e in evidence:
            body_lines.append(f"- {e}")
    # include category-specific suggestions when available
    if category_suggestions:
        body_lines.append("")
        body_lines.append("More suggestions:")
        for cat_key, score_val, text in category_suggestions:
            display = cat_key.replace("_", " ").capitalize()
            body_lines.append(f"- {display} ({score_val}/10): {text}")

    body_lines.append("")
    body_lines.append(f"Suggested next step: {lead.suggestion or 'Schedule a 15‑minute audit to prioritize fixes.'}")
    body_lines.append("")
    body_lines.append("Would you be open to a 15‑minute call to walk through a prioritized plan?")
    body_lines.append("")
    body_lines.append("Best,")
    body_lines.append("{{your_name}}")

    body = "\n".join(body_lines)
    screenshot_url = None
    if lead.screenshot_desktop:
        screenshot_url = ("/" + lead.screenshot_desktop) if not lead.screenshot_desktop.startswith("/") else lead.screenshot_desktop

    base_reply = {
        "subject": subject,
        "preview": preview,
        "body": body,
        "placeholders": {"business_name": "{{business_name}}", "website": "{{website}}", "score": "{{score}}", "screenshot_url": "{{screenshot_url}}"},
        "screenshot_url": screenshot_url,
    }

    # If AI polishing is requested, attempt to load from cache or call OpenAI to polish.
    if use_ai and getattr(analyzer, 'OPENAI_API_KEY', None):
        try:
            ensure_data_dirs()
            cache_dir = 'data/email_cache'
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f'lead_{lead_id}_polished.json')
            # return cached if exists
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r') as f:
                        return json.load(f)
                except Exception:
                    pass

            # Prepare a compact prompt to ask the model to polish into JSON
            system = (
                "You are a concise professional assistant that polishes outreach emails. "
                "Return ONLY a single JSON object with keys: subject, preview, body. No extra text."
            )
            user = json.dumps({"subject": subject, "preview": preview, "body": body})
            headers = {"Authorization": f"Bearer {analyzer.OPENAI_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                "max_tokens": 250,
                "temperature": 0.0,
            }
            resp = httpx.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=20.0)
            if resp.status_code == 200:
                j = resp.json()
                content = None
                try:
                    content = j.get('choices', [])[0].get('message', {}).get('content')
                except Exception:
                    content = None
                if content:
                    # try to parse JSON returned by model
                    cleaned = content.strip()
                    try:
                        polished = json.loads(cleaned)
                        # persist cache
                        try:
                            with open(cache_path, 'w') as f:
                                json.dump(polished, f)
                        except Exception:
                            pass
                        return polished
                    except Exception:
                        # model didn't return strict JSON — fall back to base reply
                        pass
        except Exception as e:
            logger.exception('AI polishing failed: %s', e)

    return base_reply


@app.delete('/leads/{lead_id}')
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail='Lead not found')
    # attempt to remove screenshots and last audit file
    paths = [lead.screenshot_desktop, lead.screenshot_mobile, lead.last_audit_path]
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            logger.exception('Failed to remove file %s for lead %s', p, lead_id)
    try:
        db.delete(lead)
        db.commit()
    except Exception as e:
        logger.exception('Failed to delete lead %s from DB: %s', lead_id, e)
        raise HTTPException(status_code=500, detail='Failed to delete lead')
    return {"status": "deleted", "lead_id": lead_id}


@app.delete('/leads/uncontacted')
def delete_uncontacted(db: Session = Depends(get_db)):
    # Delete all leads that have not been marked as contacted
    leads = db.query(models.Lead).filter(models.Lead.contacted == False).all()
    deleted = []
    for lead in leads:
        paths = [lead.screenshot_desktop, lead.screenshot_mobile, lead.last_audit_path]
        for p in paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                logger.exception('Failed to remove file %s for lead %s', p, lead.id)
        try:
            deleted.append(lead.id)
            db.delete(lead)
        except Exception:
            logger.exception('Failed to delete lead %s', lead.id)
    try:
        db.commit()
    except Exception:
        logger.exception('Failed to commit deletions for uncontacted leads')
        raise HTTPException(status_code=500, detail='Failed to delete uncontacted leads')
    return {"deleted_ids": deleted, "count": len(deleted)}

@app.get("/export")
def export_csv(db: Session = Depends(get_db)):
    leads = db.query(models.Lead).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "business_name", "website_url", "score", "reason", "explanation", "suggestion", "screenshot_desktop", "screenshot_mobile", "contacted", "created_at"])
    for l in leads:
        writer.writerow([l.id, l.business_name or "", l.website_url, l.score or "", l.reason or "", l.explanation or "", l.suggestion or "", l.screenshot_desktop or "", l.screenshot_mobile or "", l.contacted, l.created_at])
    output.seek(0)
    return StreamingResponse(iter([output.read()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=leads.csv"})
