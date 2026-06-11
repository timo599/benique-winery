#!/usr/bin/env python3
"""
BENIQUE Winery Tool – Wix Daily Sync
Fetches live data from Wix and patches the embedded data block in index.html.
Runs daily via GitHub Actions (see .github/workflows/wix-daily-sync.yml).

Required GitHub Secrets:
  WIX_API_KEY   – Wix API Key (IST2.eyJ...)
  WIX_SITE_ID   – Wix Site ID (c01ea708-...)
"""

import os, json, re, sys
from datetime import datetime, timezone
import requests

API_KEY  = os.environ.get('WIX_API_KEY', '')
SITE_ID  = os.environ.get('WIX_SITE_ID', 'c01ea708-0173-47bd-a1dd-07ca081165d5')
HTML_PATH = 'index.html'

if not API_KEY:
    print("⚠️  WIX_API_KEY nicht gesetzt – Sync übersprungen.")
    sys.exit(0)

HEADERS = {
    'Authorization': API_KEY,
    'wix-site-id':   SITE_ID,
    'Content-Type':  'application/json',
}

def wix_get(url):
    r = requests.get(f'https://www.wixapis.com{url}', headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def wix_post(url, body):
    r = requests.post(f'https://www.wixapis.com{url}', headers=HEADERS, json=body, timeout=20)
    r.raise_for_status()
    return r.json()

# ── Kunden ───────────────────────────────────────────────────────────────────
print("→ Fetching Kontakte...")
contacts_data = wix_get('/contacts/v4/contacts?limit=1000')
contacts_raw  = contacts_data.get('contacts', [])

GASTRO = ['restaurant','café','cafe','küche','bistro','hotel','lounge']
HANDEL = ['gmbh','kg','weinhandlung','handel','boutique','wempe','aws','e. v.','tsv']

def get_typ(name):
    nl = name.lower()
    for g in GASTRO:
        if g in nl: return 'Gastronomie'
    for h in HANDEL:
        if h in nl: return 'Handel'
    return 'Privat'

SKIP_EMAILS = {'test@test.com','wix.compreview@gmail.com','info@benique-wine.com',
               'info@benique-wine.co','info@nc-verwaltung.de','xxx@x.de'}

kunden = []
for c in contacts_raw:
    email = c.get('primaryInfo', {}).get('email', '')
    if email in SKIP_EMAILS: continue
    first = c.get('info', {}).get('name', {}).get('first', '')
    last  = c.get('info', {}).get('name', {}).get('last',  '')
    name  = (first + ' ' + last).strip() or email
    addrs = c.get('info', {}).get('addresses', {}).get('items', [])
    addr  = addrs[0].get('address', {}) if addrs else {}
    kunden.append({
        'id':          'wix_' + c['id'],
        'name':        name,
        'typ':         get_typ(name),
        'email':       email,
        'tel':         c.get('primaryInfo', {}).get('phone', ''),
        'ort':         addr.get('city', ''),
        'adresse':     addr.get('addressLine', addr.get('streetAddress', {}).get('name', '') + ' ' + addr.get('streetAddress', {}).get('number', '')).strip(),
        'umsatz':      0,
        'wixId':       c['id'],
        'quelle':      'Wix',
        'newsletter':  c.get('primaryEmail', {}).get('subscriptionStatus') == 'SUBSCRIBED',
    })

# ── Bestellungen ─────────────────────────────────────────────────────────────
print("→ Fetching Bestellungen...")
all_orders = []
cursor = None
while True:
    body = {'query': {'paging': {'limit': 100}}}
    if cursor:
        body['query']['cursorPaging'] = {'cursor': cursor}
    data = wix_post('/stores/v2/orders/query', body)
    orders = data.get('orders', [])
    for o in orders:
        buyer = o.get('buyerInfo', {})
        all_orders.append({
            'id':          o['id'][:8],
            'nr':          'WIX-' + str(o['number']),
            'datum':       (o.get('dateCreated', '')[:10]),
            'kunde':       f"{buyer.get('firstName','')} {buyer.get('lastName','')}".strip(),
            'email':       buyer.get('email', ''),
            'brutto':      str(round(float(o.get('totals', {}).get('total', 0)), 2)),
            'netto':       str(round(float(o.get('totals', {}).get('subtotal', 0)), 2)),
            'mwst':        str(round(float(o.get('totals', {}).get('tax', 0)), 2)),
            'status':      'Bezahlt' if o.get('paymentStatus') == 'PAID' else ('Storniert' if o.get('paymentStatus') == 'REFUNDED' else 'Offen'),
            'zahlungsart': o.get('billingInfo', {}).get('paymentMethod', ''),
            'betreff':     ', '.join(f"{li['name']} x{li['quantity']}" for li in o.get('lineItems', [])),
            'wixOrderId':  o['id'],
            'quelle':      'Wix',
        })
    meta = data.get('metadata', {})
    if not meta.get('hasNext'):
        break
    cursor = meta.get('cursors', {}).get('next')

# Umsatz pro Kunde berechnen
umsatz_map = {}
for b in all_orders:
    if b['status'] == 'Bezahlt' and b['email']:
        umsatz_map[b['email']] = round(umsatz_map.get(b['email'], 0) + float(b['brutto']), 2)
for k in kunden:
    k['umsatz'] = umsatz_map.get(k['email'], 0)

# ── Produkte ─────────────────────────────────────────────────────────────────
print("→ Fetching Produkte...")
try:
    prod_data = wix_post('/stores/v1/products/query', {'query': {'paging': {'limit': 100}}})
    products_raw = prod_data.get('products', [])
    produkte = [{
        'id':           'wixprod_' + p['id'][:8],
        'artnr':        'WIX-' + str(i+1).zfill(3),
        'bezeichnung':  p.get('name', ''),
        'preis_privat': float(str(p.get('price', {}).get('price', 0)).replace(',','.')),
        'preis_gastro': 0,
        'preis_handel': 0,
        'einheit':      '0,75l',
        'bestand':      0,
        'wixProductId': p['id'],
        'quelle':       'Wix',
    } for i, p in enumerate(products_raw)]
except Exception as e:
    print(f"  Produkte: {e} – übersprungen")
    produkte = []

# ── Blog Posts ───────────────────────────────────────────────────────────────
print("→ Fetching Blog Posts...")
try:
    blog_data = wix_post('/blog/v3/posts/query', {'paging': {'limit': 50}})
    posts_raw = blog_data.get('posts', [])
    blogs = [{
        'id':     'wixblog_' + p['id'][:8],
        'titel':  p.get('title', ''),
        'inhalt': re.sub(r'<[^>]+>', '', p.get('excerpt', ''))[:200],
        'datum':  p.get('firstPublishedDate', '')[:10],
        'status': 'Veröffentlicht',
        'quelle': 'Wix',
        'slug':   p.get('slug', ''),
    } for p in posts_raw]
except Exception as e:
    print(f"  Blog: {e} – übersprungen")
    blogs = []

# ── Kampagnen ─────────────────────────────────────────────────────────────────
print("→ Fetching E-Mail Kampagnen...")
try:
    camp_data = wix_get('/email-marketing/v1/campaigns?limit=50')
    camps_raw = camp_data.get('campaigns', [])
    kampagnen = [{
        'id':          'wixcampaign_' + c.get('campaignId', '')[:8],
        'name':        c.get('emailSubject', ''),
        'typ':         'E-Mail Newsletter',
        'status':      c.get('publishingData', {}).get('status', 'Gesendet'),
        'datum':       (c.get('publishingData', {}).get('scheduledDate', '') or '')[:10],
        'kanal':       'E-Mail',
        'zielgruppe':  'Newsletter-Abonnenten',
        'quelle':      'Wix',
    } for c in camps_raw]
except Exception as e:
    print(f"  Kampagnen: {e} – übersprungen")
    kampagnen = []

# ── Receipts (Direktverkäufe / manuelle Zahlungen) ────────────────────────────
print("→ Fetching Receipts (Direktverkäufe)...")
receipts_direkt = []
try:
    rc_data = wix_post('/receipts/v1/receipts/query', {
        'query': {'paging': {'limit': 100}, 'sort': [{'fieldName': 'createdDate', 'order': 'DESC'}]}
    })
    rc_raw = rc_data.get('receipts', [])
    # Nur Receipts die NICHT als Store-Order schon existieren (keine Duplikate)
    order_bruttos = {(o['datum'], o['brutto']) for o in all_orders}
    for rc in rc_raw:
        datum = rc.get('createdDate', '')[:10]
        brutto = str(round(float(rc.get('totals', {}).get('total', 0)), 2))
        if (datum, brutto) in order_bruttos:
            continue  # Bereits als Bestellung erfasst
        items = rc.get('lineItems', [])
        receipts_direkt.append({
            'id':           'rcpt_' + rc['id'][:8],
            'nr':           'R-' + datum[:4] + '-' + rc['id'][:6],
            'datum':        datum,
            'kunde':        'Direktverkauf',
            'brutto':       brutto,
            'netto':        str(round(float(rc.get('totals', {}).get('subtotal', 0)) or float(rc.get('totals', {}).get('total', 0)), 2)),
            'mwst':         str(round(float(rc.get('totals', {}).get('tax', 0)), 2)),
            'status':       'Bezahlt',
            'betreff':      ', '.join(i.get('name', '') for i in items)[:80],
            'quelle':       'Wix-Receipt',
            'wixReceiptId': rc['id'],
        })
    print(f"  → {len(rc_raw)} Receipts total, {len(receipts_direkt)} davon Direktverkäufe (nicht in Bestellungen)")
except Exception as e:
    print(f"  Receipts: {e} – übersprungen")

# ── Statistiken ───────────────────────────────────────────────────────────────
total_umsatz_orders  = sum(float(b['brutto']) for b in all_orders if b['status'] == 'Bezahlt')
total_umsatz_direkt  = sum(float(r['brutto']) for r in receipts_direkt)
total_umsatz         = round(total_umsatz_orders + total_umsatz_direkt, 2)
now_iso = datetime.now(timezone.utc).isoformat()

print(f"✅ {len(kunden)} Kunden · {len(all_orders)} Bestellungen · {len(receipts_direkt)} Direktverkäufe · {len(produkte)} Produkte · {len(blogs)} Blog-Posts · {len(kampagnen)} Kampagnen")
print(f"   Umsatz Online-Shop:    {total_umsatz_orders:.2f} €")
print(f"   Umsatz Direktverkauf:  {total_umsatz_direkt:.2f} €")
print(f"   Gesamtumsatz:          {total_umsatz:.2f} €")

# ── HTML Patchen ──────────────────────────────────────────────────────────────
print("→ Patching index.html...")
with open(HTML_PATH, 'r', encoding='utf-8') as f:
    html = f.read()

def js(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))

new_block = f"""
// ══════════════════════════════════════════════════════════════════════════════
// BENIQUE Wix Live-Daten – direkt eingebettet (Wix Site: Benique)
// Stand: {now_iso[:10]} · {len(kunden)} Kontakte · {len(all_orders)} Bestellungen · {len(receipts_direkt)} Direktverkäufe · {len(produkte)} Produkte · Umsatz {total_umsatz:.2f}€
// Automatisch generiert von scripts/wix_sync.py (GitHub Actions)
// ══════════════════════════════════════════════════════════════════════════════
(function initWixLiveDaten() {{
  if (localStorage.getItem('ew_wix_live_init') === '{now_iso[:10]}') return;
  const KUNDEN           = {js(kunden)};
  const RECHNUNGEN       = {js(all_orders)};
  const RECEIPTS_DIREKT  = {js(receipts_direkt)};
  const PRODUKTE         = {js(produkte)};
  const BLOGS            = {js(blogs)};
  const KAMPAGNEN        = {js(kampagnen)};
  const WIX_CFG          = {{"siteId":"{SITE_ID}","connected":true,"lastSync":"{now_iso}","lastInitFrom":"GitHub-Actions-Daily-Sync"}};

  function mergeByKey(existing, incoming, keyFn) {{
    const seen = new Set(existing.map(keyFn));
    return existing.concat(incoming.filter(x => !seen.has(keyFn(x))));
  }}

  // Kunden – merge, bestehende Datensätze MIT Wix-Daten updaten
  const kEx = JSON.parse(localStorage.getItem('ew_kunden') || '[]');
  const kMerged = [...kEx];
  KUNDEN.forEach(k => {{
    const idx = kMerged.findIndex(x => x.email === k.email || x.wixId === k.wixId);
    if (idx >= 0) {{ kMerged[idx] = Object.assign({{...kMerged[idx]}}, k); }}
    else {{ kMerged.push(k); }}
  }});
  localStorage.setItem('ew_kunden', JSON.stringify(kMerged));

  // Rechnungen: Online-Shop + Direktverkauf-Receipts zusammenführen
  const rEx = JSON.parse(localStorage.getItem('ew_rechnungen') || '[]');
  const allR = mergeByKey(rEx, RECHNUNGEN, r => r.wixOrderId || r.id);
  localStorage.setItem('ew_rechnungen', JSON.stringify(mergeByKey(allR, RECEIPTS_DIREKT, r => r.wixReceiptId || r.id)));

  // Produkte – immer überschreiben (Wix ist führend)
  localStorage.setItem('ew_produkte', JSON.stringify(PRODUKTE.length ? PRODUKTE : JSON.parse(localStorage.getItem('ew_produkte') || '[]')));

  // Blog-Posts – merge
  const bEx = JSON.parse(localStorage.getItem('ew_blog') || '[]');
  localStorage.setItem('ew_blog', JSON.stringify(mergeByKey(bEx, BLOGS, b => b.id)));

  // Kampagnen – merge
  const kamEx = JSON.parse(localStorage.getItem('ew_kampagnen') || '[]');
  localStorage.setItem('ew_kampagnen', JSON.stringify(mergeByKey(kamEx, KAMPAGNEN, k => k.id)));

  // Wix Config (API Key bleibt falls lokal gesetzt)
  const cfgEx = JSON.parse(localStorage.getItem('ew_wix_config') || '{{}}');
  localStorage.setItem('ew_wix_config', JSON.stringify(Object.assign({{}}, WIX_CFG, cfgEx.apiKey ? {{apiKey: cfgEx.apiKey}} : {{}})));

  localStorage.setItem('ew_wix_live_init', '{now_iso[:10]}');
  console.log('[BENIQUE] Wix-Daten initialisiert: {len(kunden)} Kunden · {len(all_orders)} Shop-Bestellungen + {len(receipts_direkt)} Direktverkäufe · {len(produkte)} Produkte · Gesamtumsatz {total_umsatz:.2f} €');
}})();"""

# Alten Block finden und ersetzen
pattern = r'// ══+\n// BENIQUE Wix Live-Daten.*?\}\)\(\);'
if re.search(pattern, html, re.DOTALL):
    html_new = re.sub(pattern, new_block.strip(), html, flags=re.DOTALL)
    print("✅ Bestehender Block ersetzt")
else:
    # Vor </script></body> einfügen
    html_new = html.replace('\n</script>\n</body>', new_block + '\n</script>\n</body>', 1)
    print("✅ Block neu eingefügt")

with open(HTML_PATH, 'w', encoding='utf-8') as f:
    f.write(html_new)

print(f"✅ index.html aktualisiert ({len(html_new):,} Bytes)")
