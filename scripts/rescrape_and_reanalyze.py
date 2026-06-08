#!/usr/bin/env python3
import os,sys,json
sys.path.insert(0, os.getcwd())
from app.db import SessionLocal
from app import scraper, analyzer, models

def main():
    db = SessionLocal()
    leads = db.query(models.Lead).all()
    print('Found', len(leads), 'leads to rescrape')
    updated = 0
    for l in leads:
        try:
            print(f'---\nRescraping lead {l.id}: {l.website_url}')
            res = scraper.scrape_url(str(l.website_url))
            # Update screenshot paths
            l.screenshot_desktop = res.get('screenshot_desktop')
            l.screenshot_mobile = res.get('screenshot_mobile')
            # Re-run analyzer using new screenshots (html not available here)
            analysis = analyzer.analyze_images(l.screenshot_desktop, l.screenshot_mobile, html='')
            l.score = analysis.get('score')
            l.reason = analysis.get('reason')
            l.explanation = analysis.get('explanation')
            l.suggestion = analysis.get('suggestion')
            blob = {'features': analysis.get('features') or {}, 'categories': analysis.get('categories') or {}}
            l.features_json = json.dumps(blob)
            db.commit()
            print(' Updated score:', l.score)
            print(' Categories:', json.dumps(blob.get('categories') or {}))
            updated += 1
        except Exception as e:
            print(' Error rescraping lead', l.id, str(e))
    print('Done. Rescraped and updated', updated, 'leads')

if __name__ == '__main__':
    main()
