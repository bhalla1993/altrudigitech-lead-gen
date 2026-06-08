#!/usr/bin/env python3
import os,sys,json
sys.path.insert(0, os.getcwd())
from app.db import SessionLocal
from app import analyzer, models, scraper

def main():
    db = SessionLocal()
    leads = db.query(models.Lead).all()
    print('Found', len(leads), 'leads')
    count = 0
    for l in leads:
        print(f'Processing lead {l.id} {l.website_url}')
        if not l.screenshot_desktop and not l.screenshot_mobile:
            print(' Skipping (no screenshots)')
            continue
        try:
            # Attempt to re-fetch the page HTML to provide richer probes for analysis
            try:
                scraped = scraper.scrape_url(str(l.website_url))
                page_html = scraped.get('html', '')
            except Exception:
                page_html = ''

            res = analyzer.analyze_images(l.screenshot_desktop, l.screenshot_mobile, html=page_html)
            l.score = res.get('score')
            l.reason = res.get('reason')
            l.explanation = res.get('explanation')
            l.suggestion = res.get('suggestion')
            blob = {'features': res.get('features') or {}, 'categories': res.get('categories') or {}}
            l.features_json = json.dumps(blob)
            db.commit()
            print(' -> score', l.score)
            print(' -> categories', json.dumps(res.get('categories') or {}))
            count += 1
        except Exception as e:
            print(' Error processing lead', l.id, e)
    print('Updated', count, 'leads')

if __name__ == '__main__':
    main()
