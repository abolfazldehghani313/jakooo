import os, uuid, re, json, random, logging, shutil, urllib.request, urllib.error
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify, Response
from models import connect, init_db

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / 'static' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('JAKO_SECRET_KEY', 'change-this-secret-key-in-production')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# لاگ امن برای Production؛ جزئیات خطا در مرورگر کاربر نمایش داده نمی‌شود.
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / 'jako.log'),
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger('jako')

def backup_database():
    """یک نسخه پشتیبان از دیتابیس محلی قبل از اجرای برنامه نگه می‌دارد."""
    try:
        db = BASE_DIR / 'jako.db'
        if not db.exists() or db.stat().st_size == 0:
            return
        backup_dir = BASE_DIR / 'backups'
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        shutil.copy2(db, backup_dir / f'jako_{stamp}.db')
        files = sorted(backup_dir.glob('jako_*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[10:]:
            old.unlink(missing_ok=True)
    except Exception:
        logger.exception('database backup failed')


# تنظیمات بازارپی از فایل جداگانه خوانده می‌شود.
# برای وارد کردن توکن فقط فایل bazaarpay_config.py را ویرایش کن.
from bazaarpay_config import (
    BAZAARPAY_API_BASE,
    BAZAARPAY_TOKEN,
    BAZAARPAY_DESTINATION,
    BAZAARPAY_TIMEOUT,
    BAZAARPAY_AMOUNT_MULTIPLIER,
)
BAZAARPAY_API_BASE = BAZAARPAY_API_BASE.rstrip('/')


init_db()
backup_database()

def get_app_settings(keys=None):
    """تنظیمات ظاهری/PWA را از دیتابیس می‌خواند."""
    con = connect()
    if keys:
        placeholders=",".join("?"*len(keys))
        rows=con.execute(f"SELECT key,value FROM app_settings WHERE key IN ({placeholders})", tuple(keys)).fetchall()
    else:
        rows=con.execute("SELECT key,value FROM app_settings").fetchall()
    con.close()
    out={r['key']:r['value'] for r in rows}
    out.setdefault('app_name','جاکو')
    out.setdefault('app_short_name','جاکو')
    out.setdefault('app_theme_color','#087d50')
    out.setdefault('app_icon','images/placeholder.svg')
    return out

@app.context_processor
def inject_app_settings():
    try:
        settings=get_app_settings()
    except Exception:
        settings={'app_name':'جاکو','app_short_name':'جاکو','app_theme_color':'#087d50','app_icon':'images/placeholder.svg'}
    return {'app_settings':settings,'app_name':settings.get('app_name','جاکو')}


@app.route('/manifest.webmanifest')
def dynamic_manifest():
    s=get_app_settings(['app_name','app_short_name','app_theme_color','app_icon'])
    icon=s.get('app_icon') or 'images/placeholder.svg'
    icon_url=url_for('static',filename=icon,_external=True)
    manifest={
        "name":s.get('app_name') or 'جاکو',
        "short_name":s.get('app_short_name') or s.get('app_name') or 'جاکو',
        "start_url":url_for('index',_external=True),
        "scope":"/",
        "display":"standalone",
        "background_color":"#ffffff",
        "theme_color":s.get('app_theme_color') or '#087d50',
        "dir":"rtl","lang":"fa",
        "icons":[
            {"src":icon_url,"sizes":"192x192","type":"image/png","purpose":"any maskable"},
            {"src":icon_url,"sizes":"512x512","type":"image/png","purpose":"any maskable"}
        ]
    }
    return Response(json.dumps(manifest,ensure_ascii=False),mimetype='application/manifest+json')

@app.route('/service-worker.js')
def root_service_worker():
    sw_path=BASE_DIR/'static'/'service-worker.js'
    try:
        body=sw_path.read_text(encoding='utf-8')
    except Exception:
        body="self.addEventListener('fetch',()=>{});"
    return Response(body,mimetype='application/javascript',headers={'Service-Worker-Allowed':'/'})


def get_bazaarpay_settings():
    """تنظیمات بازارپی را از پنل مدیریت بخوان؛ در صورت نبود تنظیم، از فایل تنظیمات استفاده کن."""
    con = connect()
    rows = con.execute("SELECT key,value FROM app_settings WHERE key IN ('bazaarpay_token','bazaarpay_destination','bazaarpay_enabled','bazaarpay_amount_multiplier')").fetchall()
    con.close()
    values = {r['key']: r['value'] for r in rows}
    token = values.get('bazaarpay_token')
    destination = values.get('bazaarpay_destination') or BAZAARPAY_DESTINATION
    enabled_value = values.get('bazaarpay_enabled')
    if enabled_value is None:
        enabled = bool(BAZAARPAY_TOKEN)
    else:
        enabled = enabled_value == '1'
    multiplier_raw = values.get('bazaarpay_amount_multiplier')
    try:
        multiplier = max(1, int(multiplier_raw or BAZAARPAY_AMOUNT_MULTIPLIER))
    except (TypeError, ValueError):
        multiplier = max(1, int(BAZAARPAY_AMOUNT_MULTIPLIER))
    # اگر قبلاً از bazaarpay_config.py توکن تنظیم شده ولی در پنل چیزی ذخیره نشده،
    # برای سازگاری همان توکن استفاده می‌شود.
    if token is None:
        token = BAZAARPAY_TOKEN
    return {
        'token': token or '',
        'destination': destination,
        'enabled': enabled,
        'amount_multiplier': multiplier,
        'configured': bool(token and enabled),
    }


def set_bazaarpay_setting(key, value):
    con = connect()
    con.execute(
        "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )
    con.commit()
    con.close()


def bazaarpay_request(path, payload, authenticated=False):
    settings = get_bazaarpay_settings()
    url = f"{BAZAARPAY_API_BASE}/{path.strip('/')}/"
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type':'application/json','Accept':'application/json','User-Agent':'Jako/1.0 BazaarPay'}
    if authenticated and settings['token']:
        headers['Authorization'] = f"Token {settings['token']}"
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=BAZAARPAY_TIMEOUT) as response:
            raw=response.read().decode('utf-8')
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw=e.read().decode('utf-8', errors='replace')
        try: data=json.loads(raw)
        except Exception: data={'detail':raw or f'HTTP {e.code}'}
        return e.code,data
    except Exception as e:
        return 0,{'detail':str(e)}

def bazaarpay_init_checkout(amount_toman, service_name):
    settings = get_bazaarpay_settings()
    if not settings['configured']:
        return 0, {'detail': 'درگاه بازارپی فعال نیست. از پنل مدیریت > تنظیمات درگاه پرداخت، توکن را وارد و درگاه را فعال کنید.'}
    multiplier = settings['amount_multiplier']
    return bazaarpay_request('checkout/init', {
        'amount': int(amount_toman) * multiplier,
        'destination': settings['destination'],
        'service_name': service_name[:512]
    }, authenticated=bool(settings['token']))

def bazaarpay_trace(checkout_token):
    return bazaarpay_request('trace', {'checkout_token':checkout_token}, authenticated=False)

def bazaarpay_commit(checkout_token):
    return bazaarpay_request('commit', {'checkout_token':checkout_token}, authenticated=True)

def bazaarpay_refund(checkout_token, amount=None):
    payload={'checkout_token':checkout_token}
    if amount:
        payload['amount']=int(amount)*get_bazaarpay_settings()['amount_multiplier']
    return bazaarpay_request('refund',payload,authenticated=True)

def local_payment_row(payment_id):
    con=connect(); r=con.execute('SELECT * FROM payments WHERE id=?',(payment_id,)).fetchone(); con.close(); return r

def create_bazaarpay_payment(user_id, kind, amount, description, meta=None):
    settings = get_bazaarpay_settings()
    if not settings['configured']:
        raise RuntimeError('درگاه بازارپی فعال نیست. مدیر باید از منوی تنظیمات درگاه پرداخت، توکن را وارد و درگاه را فعال کند.')
    meta=meta or {}; ref=f'JAKO-{uuid.uuid4().hex[:16].upper()}'
    con=connect()
    cur=con.execute(
        "INSERT INTO payments(user_id,kind,amount,status,ref,gateway,metadata) VALUES(?,?,?,?,?,?,?)",
        (user_id,kind,int(amount),'pending',ref,'bazaarpay',json.dumps(meta,ensure_ascii=False))
    )
    payment_id=cur.lastrowid; con.commit(); con.close()
    code,data=bazaarpay_init_checkout(int(amount),description)
    if code!=200 or not data.get('checkout_token') or not data.get('payment_url'):
        con=connect(); con.execute("UPDATE payments SET status=?,gateway_message=? WHERE id=?",
                                   ('failed',str(data.get('detail','خطا در ایجاد پرداخت بازارپی')),payment_id))
        con.commit(); con.close()
        raise RuntimeError(data.get('detail','ایجاد پرداخت بازارپی ناموفق بود.'))
    con=connect(); con.execute("UPDATE payments SET checkout_token=?,payment_url=? WHERE id=?",
                               (data['checkout_token'],data['payment_url'],payment_id))
    con.commit(); con.close()
    return payment_id,data['payment_url']

def finalize_bazaarpay_payment(payment_id):
    con=connect(); p=con.execute('SELECT * FROM payments WHERE id=?',(payment_id,)).fetchone(); con.close()
    if not p: return False,'پرداخت پیدا نشد.'
    if p['status'] in ('paid','committed'): return True,'پرداخت قبلاً تأیید شده است.'
    if not p['checkout_token']: return False,'توکن پرداخت موجود نیست.'
    code,data=bazaarpay_trace(p['checkout_token']); status=data.get('status')
    if code!=200: return False,data.get('detail','خطا در استعلام پرداخت.')
    if status=='unpaid': return False,'پرداخت هنوز نهایی نشده است.'
    if status in ('paid_not_committed','paid_committed'):
        con=connect(); con.execute("UPDATE payments SET status=?,gateway_status=? WHERE id=?",
                                   ('paid',status,payment_id)); con.commit(); con.close()
        if status!='paid_committed':
            code2,data2=bazaarpay_commit(p['checkout_token'])
            if code2 not in (200,204):
                return False,data2.get('detail','پرداخت انجام شد ولی تأیید نهایی بازارپی ناموفق بود.')
        con=connect(); con.execute("UPDATE payments SET status=?,gateway_status=?,committed_at=CURRENT_TIMESTAMP WHERE id=?",
                                   ('committed','paid_committed',payment_id)); con.commit(); con.close()
        return True,'پرداخت با موفقیت تأیید شد.'
    if status in ('refunded','paid_not_committed_refunded','timed_out','invalid_token'):
        con=connect(); con.execute("UPDATE payments SET status=?,gateway_status=? WHERE id=?",
                                   ('failed',status,payment_id)); con.commit(); con.close()
        return False,'پرداخت موفق نهایی نشده یا بازگشت داده شده است.'
    return False,f'وضعیت پرداخت: {status or "نامشخص"}'


@app.errorhandler(404)
def page_not_found(error):
    return render_template('error.html', code=404, message='صفحه موردنظر پیدا نشد.'), 404

@app.errorhandler(403)
def forbidden(error):
    return render_template('error.html', code=403, message='دسترسی به این بخش مجاز نیست.'), 403

@app.errorhandler(413)
def too_large(error):
    return render_template('error.html', code=413, message='حجم فایل بیش از حد مجاز است.'), 413

@app.errorhandler(500)
def internal_error(error):
    logger.exception('Unhandled application error')
    return render_template('error.html', code=500, message='مشکلی در پردازش درخواست پیش آمد. لطفاً دوباره تلاش کنید.'), 500

# دسته‌بندی‌های اصلی جاکو؛ هر دسته فیلترهای اختصاصی خودش را در صفحه جستجو نشان می‌دهد.
CATEGORIES = [
    ('all','همه','grid'),
    ('stay','اقامتگاه','villa'),
    ('car','اجاره ماشین','car'),
    ('equipment','تجهیزات و لوازم','tools'),
    ('gaming','لوازم گیمینگ','gamepad'),
    ('camera','دوربین و تصویربرداری','camera'),
    ('events','جشن و مراسم','events'),
    ('camp','کمپ و سفر','camp'),
    ('sport','ورزش و تفریح','sport'),
]
KHZ_CITIES = [
    'اهواز','دزفول','شوش','اندیمشک','آبادان','خرمشهر','ماهشهر','شادگان','مسجدسلیمان',
    'ایذه','بهبهان','رامهرمز','رامشیر','شوشتر','گتوند','لالی','اندیکا','هفتکل',
    'هندیجان','آغاجاری','حمیدیه','بستان','سوسنگرد','دشت آزادگان','امیدیه','دورق'
]
ALLOWED_IMAGES = {'png','jpg','jpeg','webp'}

# تعرفه تبلیغات: مدت‌زمان ← قیمت تومان
AD_PRICES = {
    1: 500_000,
    6: 1_800_000,
    12: 3_000_000,
    24: 5_000_000,
}

# تعرفه قطعی استوری
STORY_PRICES = {1: 25_000, 6: 50_000, 12: 80_000, 24: 100_000}
STORY_PRICE_LABELS = {1: '۱ ساعت', 6: '۶ ساعت', 12: '۱۲ ساعت', 24: '۲۴ ساعت'}


AD_PRICE_LABELS = {1: '۱ ساعت', 6: '۶ ساعت', 12: '۱۲ ساعت', 24: '۲۴ ساعت'}

# تعرفه‌ها از پنل مدیریت قابل تغییر هستند. این مقادیر فقط پیش‌فرض اولیه‌اند.
DEFAULT_STORY_TARIFFS = [
    {'duration': 1, 'price': 25_000},
    {'duration': 6, 'price': 50_000},
    {'duration': 12, 'price': 80_000},
    {'duration': 24, 'price': 100_000},
]
DEFAULT_AD_TARIFFS = [
    {'duration': 1, 'price': 500_000},
    {'duration': 6, 'price': 1_800_000},
    {'duration': 12, 'price': 3_000_000},
    {'duration': 24, 'price': 5_000_000},
]

def _get_setting(key, default=''):
    con = connect()
    r = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    con.close()
    return r['value'] if r else default

def _set_setting(key, value):
    con = connect()
    con.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
    con.commit(); con.close()

def _load_tariffs(setting_key, defaults):
    raw = _get_setting(setting_key, '')
    if raw:
        try:
            data = json.loads(raw)
            out = []
            for x in data:
                d = int(x.get('duration', 0)); p = max(0, int(x.get('price', 0)))
                if d > 0:
                    out.append({'duration': d, 'price': p})
            if out:
                return sorted(out, key=lambda x: x['duration'])
        except Exception:
            pass
    return list(defaults)

def get_story_tariffs():
    return _load_tariffs('story_tariffs', DEFAULT_STORY_TARIFFS)

def get_ad_tariffs():
    return _load_tariffs('ad_tariffs', DEFAULT_AD_TARIFFS)

def tariff_map(tariffs):
    return {int(x['duration']): int(x['price']) for x in tariffs}

def tariff_labels(tariffs):
    return {int(x['duration']): f"{int(x['duration'])} ساعت" for x in tariffs}


class RowObj:
    def __init__(self, row=None):
        if row:
            for k in row.keys(): setattr(self, k, row[k])
    def __getattr__(self, name): return None

def obj(row): return RowObj(row) if row else None

def user_by_id(uid):
    if not uid: return None
    con=connect(); r=con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); con.close(); return obj(r)

def active_subscription(uid):
    if not uid: return None
    con=connect(); r=con.execute('''SELECT s.*, p.name plan_name, p.price plan_price, p.duration_days FROM subscriptions s JOIN subscription_plans p ON p.id=s.plan_id WHERE s.user_id=? AND s.status='active' AND datetime(s.ends_at)>=datetime('now') ORDER BY s.id DESC LIMIT 1''',(uid,)).fetchone(); con.close(); return obj(r)

def notify(uid, title, body=''):
    con=connect(); con.execute('INSERT INTO notifications(user_id,title,body) VALUES(?,?,?)',(uid,title,body)); con.commit(); con.close()


def trust_score(user_id):
    con=connect(); u=con.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
    if not u: con.close(); return 0
    bookings=con.execute('SELECT COUNT(*) c FROM bookings WHERE user_id=? AND status IN ("confirmed","completed")',(user_id,)).fetchone()['c']
    reviews=con.execute('SELECT COUNT(*) c FROM reviews WHERE user_id=?',(user_id,)).fetchone()['c']
    score=min(100,20+bookings*10+reviews*5+(25 if u['phone_verified'] else 0)+(25 if u['identity_verified'] else 0))
    con.close(); return score

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            flash('برای ادامه وارد حساب خود شوید.', 'warning'); return redirect(url_for('login', next=request.path))
        u=user_by_id(session['user_id'])
        if not u or not u.is_active:
            session.clear(); flash('این حساب فعال نیست.', 'danger'); return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped

def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            u=user_by_id(session.get('user_id'))
            if not u: return redirect(url_for('login'))
            if u.role not in roles: abort(403)
            return view(*args, **kwargs)
        return wrapped
    return deco

def save_images(files, listing_id):
    con=connect(); count=0
    for i, f in enumerate(files):
        if not f or not f.filename: continue
        ext=f.filename.rsplit('.',1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_IMAGES: continue
        name=f'{listing_id}_{uuid.uuid4().hex}.{ext}'
        f.save(UPLOAD_DIR / name)
        con.execute('INSERT INTO images(listing_id,filename,sort_order) VALUES(?,?,?)',(listing_id,name,i)); count += 1
    con.commit(); con.close(); return count

def google_maps_directions_url(listing):
    """ساخت لینک مسیریابی مستقیم Google Maps بدون نیاز به API Key."""
    from urllib.parse import quote
    lat = getattr(listing, 'latitude', None)
    lng = getattr(listing, 'longitude', None)
    if lat is not None and lng is not None:
        destination = f"{float(lat):.6f},{float(lng):.6f}"
    else:
        parts = [getattr(listing, 'address', ''), getattr(listing, 'area', ''), getattr(listing, 'city', '')]
        destination = ', '.join(str(x).strip() for x in parts if x and str(x).strip())
    return 'https://www.google.com/maps/dir/?api=1&destination=' + quote(destination, safe='')

def attach_listing_data(listing):
    con=connect(); imgs=con.execute('SELECT * FROM images WHERE listing_id=? ORDER BY sort_order,id',(listing.id,)).fetchall(); con.close()
    listing.images=[obj(x) for x in imgs]
    return listing

@app.before_request
def expire_time_based_services():
    # انقضای خودکار سرویس‌های زمان‌دار؛ نمایش نیز بر اساس status کنترل می‌شود.
    con=connect()
    con.execute("UPDATE stories SET status='expired' WHERE status='active' AND ends_at IS NOT NULL AND datetime(ends_at)<datetime('now')")
    con.execute("UPDATE ads SET status='expired' WHERE status='active' AND ends_at IS NOT NULL AND datetime(ends_at)<datetime('now')")
    con.execute("UPDATE subscriptions SET status='expired' WHERE status='active' AND datetime(ends_at)<datetime('now')")
    con.execute("UPDATE blue_tick_subscriptions SET status='expired' WHERE status='active' AND datetime(ends_at)<datetime('now')")
    con.execute("UPDATE listings SET status='suspended',workflow_status='expired' WHERE expires_at IS NOT NULL AND datetime(expires_at)<datetime('now') AND workflow_status='approved'")
    con.execute("UPDATE users SET blue_tick_active=0, blue_tick_ends_at=NULL WHERE blue_tick_active=1 AND blue_tick_ends_at IS NOT NULL AND datetime(blue_tick_ends_at)<datetime('now')")
    con.commit(); con.close()

@app.context_processor
def inject_globals():
    u=user_by_id(session.get('user_id'))
    unread=0
    unread_messages=0
    if u:
        con=connect(); unread=con.execute('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0',(u.id,)).fetchone()['c']; unread_messages=con.execute('SELECT COUNT(*) c FROM messages WHERE receiver_id=? AND is_read=0',(u.id,)).fetchone()['c']; con.close()
    open_tickets = 0
    if u:
        con=connect()
        open_tickets=con.execute('SELECT COUNT(*) c FROM support_tickets WHERE user_id=? AND status!="closed"',(u.id,)).fetchone()['c']
        con.close()
    return {'current_user':u,'current_year':datetime.now().year,'unread_notifications':unread,'unread_messages':unread_messages,'active_subscription':active_subscription(u.id) if u and u.role in ('host','admin') else None,'open_tickets':open_tickets,'google_maps_directions_url':google_maps_directions_url,
            'blue_tick_active': bool(u and getattr(u,'blue_tick_active',0) and getattr(u,'blue_tick_ends_at',None) and datetime.fromisoformat(u.blue_tick_ends_at)>=datetime.now())}


@app.route('/')
def index():
    selected_city = request.args.get('city', '').strip()
    con=connect()
    ads=con.execute("SELECT a.id ad_id, a.*, l.* FROM ads a JOIN listings l ON l.id=a.listing_id WHERE a.status='active' AND datetime(a.starts_at)<=datetime('now') AND datetime(a.ends_at)>=datetime('now') AND l.status='published' ORDER BY a.id DESC LIMIT 6").fetchall()
    for _ad in ads:
        con.execute('UPDATE ads SET impressions=impressions+1 WHERE id=?',(_ad['id'],))
    promoted=con.execute("SELECT l.* FROM listing_promotions p JOIN listings l ON l.id=p.listing_id WHERE p.status='active' AND datetime(p.ends_at)>=datetime('now') AND l.status='published' ORDER BY p.id DESC LIMIT 8").fetchall()
    base_q='SELECT * FROM listings WHERE status="published"'
    params=[]
    if selected_city:
        base_q += ' AND city=?'
        params.append(selected_city)
    rows=con.execute(base_q+' ORDER BY rating DESC, id DESC LIMIT 12', params).fetchall()
    story_rows=con.execute("SELECT s.*, l.title listing_title, l.city, l.area, u.full_name host_name, i.filename listing_image FROM stories s LEFT JOIN listings l ON l.id=s.listing_id JOIN users u ON u.id=s.host_id LEFT JOIN images i ON i.listing_id=l.id AND i.sort_order=0 WHERE s.status='active' AND datetime(s.ends_at)>=datetime('now') ORDER BY s.id DESC LIMIT 12").fetchall()
    if selected_city:
        story_rows=con.execute("SELECT s.*, l.title listing_title, l.city, l.area, u.full_name host_name, i.filename listing_image FROM stories s LEFT JOIN listings l ON l.id=s.listing_id JOIN users u ON u.id=s.host_id LEFT JOIN images i ON i.listing_id=l.id AND i.sort_order=0 WHERE s.status='active' AND datetime(s.ends_at)>=datetime('now') AND (l.city=? OR l.city IS NULL) ORDER BY s.id DESC LIMIT 12",(selected_city,)).fetchall()
    # فقط استوری‌هایی که واقعاً توسط میزبان ایجاد و تأیید/فعال شده‌اند نمایش داده شوند.
    # آگهی جدید نباید به‌صورت خودکار به استوری تبدیل یا در این بخش شبیه استوری نمایش داده شود.
    slides=con.execute("SELECT * FROM home_slides WHERE is_active=1 ORDER BY sort_order,id").fetchall()
    con.close()
    featured=[attach_listing_data(obj(r)) for r in rows]
    sponsored=[attach_listing_data(obj(r)) for r in ads]
    promoted_listings=[attach_listing_data(obj(r)) for r in promoted]
    stories=[obj(r) for r in story_rows]
    home_slides=[obj(r) for r in slides]
    db_categories=[(r['slug'],r['name'],r['icon_svg']) for r in connect().execute("SELECT slug,name,icon_svg FROM categories WHERE is_active=1 AND parent_id IS NULL ORDER BY sort_order,id").fetchall()]
    con2=connect(); db_cities=[r['name'] for r in con2.execute("SELECT DISTINCT name FROM cities WHERE is_active=1 ORDER BY sort_order,id").fetchall()]; con2.close()
    app_settings=get_app_settings()
    return render_template('index.html', featured_listings=featured, sponsored_listings=sponsored,
                           promoted_listings=promoted_listings, stories=stories, categories=db_categories,
                           cities=db_cities, selected_city=selected_city, home_slides=home_slides,
                           home_banner=app_settings)
@app.route('/terms')
def terms():
    con=connect(); row=con.execute("SELECT * FROM content_pages WHERE key='terms'").fetchone(); con.close()
    return render_template('terms.html', custom_content=row['body'] if row else '')

@app.route('/privacy')
def privacy():
    con=connect(); row=con.execute("SELECT * FROM content_pages WHERE key='privacy'").fetchone(); con.close()
    return render_template('privacy.html', custom_content=row['body'] if row else '')

def _otp_rate_ok(mobile):
    now = datetime.now()
    con = connect()
    row = con.execute(
        "SELECT sent_at,attempts FROM otp_requests WHERE mobile=?",
        (mobile,)
    ).fetchone()
    if row:
        try:
            sent = datetime.fromisoformat(row['sent_at'])
        except Exception:
            sent = now - timedelta(minutes=10)
        if (now - sent).total_seconds() < 60:
            con.close()
            return False, 'برای ارسال مجدد کد حداقل ۶۰ ثانیه صبر کنید.'
        if int(row['attempts'] or 0) >= 10:
            con.close()
            return False, 'تعداد درخواست‌های OTP برای این شماره بیش از حد مجاز است. کمی بعد دوباره تلاش کنید.'
    con.close()
    return True, ''

def _record_otp_sent(mobile):
    con=connect()
    con.execute(
        "INSERT INTO otp_requests(mobile,sent_at,attempts) VALUES(?,?,1) "
        "ON CONFLICT(mobile) DO UPDATE SET sent_at=excluded.sent_at, attempts=otp_requests.attempts+1",
        (mobile, datetime.now().isoformat(timespec='seconds'))
    )
    con.commit(); con.close()

def _send_registration_otp(mobile):
    """ارسال OTP ثبت‌نام از طریق sms.ir."""
    con=connect()
    rows=con.execute("SELECT key,value FROM app_settings WHERE key IN ('sms_api_key','sms_otp_template_id')").fetchall()
    con.close()
    settings={r['key']:r['value'] for r in rows}
    api_key=settings.get('sms_api_key','').strip()
    template_id=settings.get('sms_otp_template_id','').strip()
    if not api_key or not template_id:
        return False, 'سرویس پیامک OTP در تنظیمات مدیریت کامل نشده است.'
    mobile=re.sub(r'\D','',mobile)
    if not re.fullmatch(r'09\d{9}', mobile):
        return False, 'شماره موبایل معتبر نیست.'
    code=f"{random.randint(10000,99999)}"
    payload={'mobile':mobile,'templateId':int(template_id),'parameters':[{'name':'CODE','value':code}]}
    req=urllib.request.Request(
        'https://api.sms.ir/v1/send/verify',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type':'application/json','Accept':'application/json','x-api-key':api_key},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req,timeout=15) as resp:
            raw=resp.read().decode('utf-8',errors='replace')
            if 200 <= resp.status < 300:
                return True, code
            return False, 'ارسال کد OTP ناموفق بود.'
    except urllib.error.HTTPError:
        return False, 'ارسال کد OTP ناموفق بود؛ تنظیمات پنل پیامکی را بررسی کنید.'
    except Exception:
        return False, 'ارتباط با سرویس پیامک برقرار نشد. دوباره تلاش کنید.'

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        full_name=request.form.get('full_name','').strip()
        mobile=re.sub(r'\D','',request.form.get('mobile',''))
        email=request.form.get('email','').strip().lower() or None
        role=request.form.get('account_type','guest')
        password=request.form.get('password','')
        confirm=request.form.get('confirm_password','')

        if not full_name or not re.fullmatch(r'09\d{9}', mobile) or role not in ('guest','host'):
            flash('نام، شماره موبایل معتبر و نوع حساب را کامل کنید.', 'danger')
            return render_template('register.html')
        if len(password)<6 or password != confirm:
            flash('رمز عبور باید حداقل ۶ کاراکتر باشد و با تکرار رمز یکسان باشد.', 'danger')
            return render_template('register.html')
        if request.form.get('accept_terms')!='1' or request.form.get('accept_privacy')!='1':
            flash('پذیرش قوانین و حریم خصوصی الزامی است.', 'danger')
            return render_template('register.html')

        con=connect()
        exists=con.execute('SELECT 1 FROM users WHERE mobile=? OR (email IS NOT NULL AND email=?)',(mobile,email)).fetchone()
        con.close()
        if exists:
            flash('این شماره موبایل یا ایمیل قبلاً ثبت شده است.', 'danger')
            return render_template('register.html')

        rate_ok, rate_message = _otp_rate_ok(mobile)
        if not rate_ok:
            flash(rate_message, 'warning')
            return render_template('register.html')
        ok, result=_send_registration_otp(mobile)
        if not ok:
            flash(result, 'danger')
            return render_template('register.html')

        _record_otp_sent(mobile)
        session['pending_registration']={
            'full_name':full_name,
            'mobile':mobile,
            'email':email,
            'role':role,
            'password_hash':generate_password_hash(password),
            'otp_hash':generate_password_hash(result),
            'otp_expires':(datetime.now()+timedelta(minutes=5)).isoformat(timespec='seconds'),
            'otp_attempts':0
        }
        flash('کد تأیید به شماره موبایل شما ارسال شد. کد را وارد کنید.', 'success')
        return redirect(url_for('register_verify'))
    return render_template('register.html')


@app.post('/register/resend-otp')
def register_resend_otp():
    pending=session.get('pending_registration')
    if not pending:
        return redirect(url_for('register'))
    mobile=pending.get('mobile','')
    rate_ok, message=_otp_rate_ok(mobile)
    if not rate_ok:
        flash(message,'warning')
        return redirect(url_for('register_verify'))
    ok, result=_send_registration_otp(mobile)
    if not ok:
        flash(result,'danger')
        return redirect(url_for('register_verify'))
    _record_otp_sent(mobile)
    pending['otp_hash']=generate_password_hash(result)
    pending['otp_expires']=(datetime.now()+timedelta(minutes=5)).isoformat(timespec='seconds')
    pending['otp_attempts']=0
    session['pending_registration']=pending
    flash('کد جدید ارسال شد.','success')
    return redirect(url_for('register_verify'))

@app.route('/register/verify', methods=['GET','POST'])
def register_verify():
    pending=session.get('pending_registration')
    if not pending:
        return redirect(url_for('register'))

    if request.method=='POST':
        code=re.sub(r'\D','',request.form.get('otp',''))
        pending['otp_attempts']=int(pending.get('otp_attempts',0))+1
        if pending['otp_attempts'] > 5:
            session.pop('pending_registration',None)
            flash('تعداد تلاش‌ها بیش از حد مجاز بود. ثبت‌نام را دوباره شروع کنید.', 'danger')
            return redirect(url_for('register'))
        try:
            expired=datetime.now() > datetime.fromisoformat(pending['otp_expires'])
        except Exception:
            expired=True
        if expired or not check_password_hash(pending['otp_hash'],code):
            session['pending_registration']=pending
            flash('کد تأیید نادرست یا منقضی شده است.', 'danger')
            return render_template('register_verify.html', mobile=pending['mobile'])

        con=connect()
        if con.execute('SELECT 1 FROM users WHERE mobile=? OR (email IS NOT NULL AND email=?)',(pending['mobile'],pending['email'])).fetchone():
            con.close()
            session.pop('pending_registration',None)
            flash('این شماره موبایل یا ایمیل قبلاً ثبت شده است.', 'danger')
            return redirect(url_for('register'))

        trial_end=None
        con.execute(
            'INSERT INTO users(full_name,mobile,email,password_hash,role,accepted_terms,accepted_privacy,host_trial_ends_at,free_host_trial_used,phone_verified) VALUES(?,?,?,?,?,?,?,?,?,1)',
            (pending['full_name'],pending['mobile'],pending['email'],pending['password_hash'],pending['role'],1,1,None,1 if pending['role']=='host' else 0)
        )
        uid=con.execute('SELECT last_insert_rowid()').fetchone()[0]

        # پلن آزمایشی رایگان میزبان: فقط یک بار و فقط هنگام ورود/ثبت‌نام اولیه میزبان.
        if pending['role']=='host':
            plan=con.execute("SELECT id,duration_days FROM subscription_plans WHERE is_trial=1 AND is_active=1 ORDER BY id LIMIT 1").fetchone()
            if plan:
                now=datetime.now(); end=now+timedelta(days=plan['duration_days'])
                con.execute(
                    'INSERT INTO subscriptions(user_id,plan_id,starts_at,ends_at,status,transaction_ref) VALUES(?,?,?,?,?,?)',
                    (uid,plan['id'],now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),'active','FREE-HOST-TRIAL')
                )
                con.execute('UPDATE users SET host_trial_ends_at=? WHERE id=?',(end.isoformat(timespec='seconds'),uid))
        con.commit(); con.close()
        session.pop('pending_registration',None)
        session['user_id']=uid
        flash('ثبت‌نام و تأیید شماره موبایل با موفقیت انجام شد.', 'success')
        return redirect(url_for('profile'))

    return render_template('register_verify.html', mobile=pending['mobile'])

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        identifier=request.form.get('identifier','').strip().lower()
        password=request.form.get('password','')
        selected=request.form.get('account_type','guest')
        con=connect(); u=con.execute('SELECT * FROM users WHERE mobile=? OR lower(email)=?',(identifier,identifier)).fetchone(); con.close()
        if not u or not u['is_active'] or not check_password_hash(u['password_hash'], password):
            flash('شماره/ایمیل یا رمز عبور اشتباه است.', 'danger'); return render_template('login.html')
        if u['role'] != 'admin' and u['role'] != selected:
            flash('نوع حساب انتخاب‌شده با حساب شما مطابقت ندارد.', 'danger'); return render_template('login.html')
        session['user_id']=u['id']; flash('خوش آمدید.', 'success'); nxt=request.args.get('next') or request.form.get('next'); return redirect(nxt if nxt and nxt.startswith('/') else url_for('profile'))
    return render_template('login.html')


@app.route('/logout')
def logout(): session.clear(); flash('از حساب خارج شدید.', 'success'); return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    u=user_by_id(session['user_id'])
    con=connect()
    bc=con.execute('SELECT COUNT(*) c FROM bookings WHERE user_id=?',(u.id,)).fetchone()['c']
    fc=con.execute('SELECT COUNT(*) c FROM favorites WHERE user_id=?',(u.id,)).fetchone()['c']
    lc=con.execute('SELECT COUNT(*) c FROM listings WHERE host_id=?',(u.id,)).fetchone()['c']
    host_rating=con.execute('SELECT COALESCE(AVG(r.rating),0) a FROM reviews r JOIN listings l ON l.id=r.listing_id WHERE l.host_id=?',(u.id,)).fetchone()['a']
    con.close()
    return render_template('profile.html', user=u, bookings_count=bc, favorites_count=fc, listings_count=lc, host_rating=round(host_rating or 0,1))

@app.route('/profile/edit', methods=['GET','POST'])
@login_required
def profile_edit():
    u=user_by_id(session['user_id'])
    if request.method=='POST':
        full_name=request.form.get('full_name','').strip() or u.full_name
        email=request.form.get('email','').strip().lower() or None
        photo=request.files.get('profile_photo')
        filename=getattr(u,'profile_photo','') or ''
        if photo and photo.filename:
            ext=photo.filename.rsplit('.',1)[-1].lower() if '.' in photo.filename else ''
            if ext not in ALLOWED_IMAGES:
                flash('فرمت عکس پروفایل مجاز نیست.','danger'); return redirect(url_for('profile_edit'))
            filename=f'profile_{u.id}_{uuid.uuid4().hex}.{ext}'
            photo.save(UPLOAD_DIR/filename)
            old=getattr(u,'profile_photo','') or ''
            if old:
                try: (UPLOAD_DIR/old).unlink(missing_ok=True)
                except Exception: pass
        con=connect()
        try:
            con.execute('UPDATE users SET full_name=?,email=?,profile_photo=? WHERE id=?',(full_name,email,filename,u.id))
            con.commit()
        except Exception as e:
            con.close(); flash('ذخیره اطلاعات پروفایل ناموفق بود.','danger'); return redirect(url_for('profile_edit'))
        con.close(); flash('پروفایل با موفقیت به‌روزرسانی شد.','success'); return redirect(url_for('profile'))
    return render_template('profile_edit.html', user=u)

@app.route('/notifications')
@login_required
def notifications():
    con=connect(); rows=con.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC',(session['user_id'],)).fetchall(); con.execute('UPDATE notifications SET is_read=1 WHERE user_id=?',(session['user_id'],)); con.commit(); con.close(); return render_template('notifications.html', notifications=[obj(x) for x in rows])

@app.route('/host-panel')
@login_required
@role_required('host','admin')
def host_panel():
    u=user_by_id(session['user_id'])
    con=connect()
    rows=con.execute('SELECT * FROM listings WHERE host_id=? ORDER BY id DESC',(u.id,)).fetchall()
    bookings=con.execute('''SELECT b.*, l.title, u.full_name guest_name FROM bookings b JOIN listings l ON l.id=b.listing_id JOIN users u ON u.id=b.user_id WHERE l.host_id=? ORDER BY b.id DESC''',(u.id,)).fetchall()
    rev=con.execute('SELECT COALESCE(SUM(total_price),0) r FROM bookings WHERE listing_id IN (SELECT id FROM listings WHERE host_id=?) AND status IN ("confirmed","completed")',(u.id,)).fetchone()['r']
    ads=con.execute('SELECT a.*,l.title FROM ads a JOIN listings l ON l.id=a.listing_id WHERE a.host_id=? ORDER BY a.id DESC',(u.id,)).fetchall()
    stories=con.execute('SELECT s.*,l.title listing_title FROM stories s LEFT JOIN listings l ON l.id=s.listing_id WHERE s.host_id=? ORDER BY s.id DESC',(u.id,)).fetchall()
    promotions=con.execute('SELECT p.*,l.title FROM listing_promotions p JOIN listings l ON l.id=p.listing_id WHERE p.host_id=? AND datetime(p.ends_at)>=datetime("now") ORDER BY p.id DESC',(u.id,)).fetchall()
    pending_bookings=con.execute('SELECT COUNT(*) c FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE l.host_id=? AND b.status="pending"',(u.id,)).fetchone()['c']
    published=con.execute('SELECT COUNT(*) c FROM listings WHERE host_id=? AND status="published"',(u.id,)).fetchone()['c']
    views=con.execute('SELECT COALESCE(SUM(impressions),0) c FROM ads WHERE host_id=?',(u.id,)).fetchone()['c']
    payouts=con.execute('SELECT * FROM payout_requests WHERE user_id=? ORDER BY id DESC',(u.id,)).fetchall()
    con.close()
    return render_template('host_panel.html', listings=[attach_listing_data(obj(x)) for x in rows], bookings=[obj(x) for x in bookings], revenue=rev, ads=[obj(x) for x in ads], stories=[obj(x) for x in stories], promotions=[obj(x) for x in promotions], pending_bookings=pending_bookings, published_count=published, ad_impressions=views, payouts=[obj(x) for x in payouts])


@app.route('/admin/tariffs', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_tariffs():
    if request.method == 'POST':
        def read_rows(prefix):
            rows=[]
            for i in range(1, 9):
                d=request.form.get(f'{prefix}_duration_{i}','').strip()
                p=request.form.get(f'{prefix}_price_{i}','').strip()
                if not d and not p:
                    continue
                try:
                    d=int(d); p=max(0,int(p or 0))
                except ValueError:
                    continue
                if d > 0:
                    rows.append({'duration':d,'price':p})
            # حذف تکراری‌ها با نگه‌داشتن آخرین مقدار
            uniq={x['duration']:x['price'] for x in rows}
            return [{'duration':d,'price':p} for d,p in sorted(uniq.items())]
        stories=read_rows('story')
        ads=read_rows('ad')
        if stories: _set_setting('story_tariffs', json.dumps(stories, ensure_ascii=False))
        if ads: _set_setting('ad_tariffs', json.dumps(ads, ensure_ascii=False))
        flash('تعرفه‌ها و تنظیمات نقشه با موفقیت ذخیره شد.','success')
        return redirect(url_for('admin_tariffs'))
    con=connect()
    plans=[obj(x) for x in con.execute('SELECT * FROM subscription_plans ORDER BY id').fetchall()]
    blue_tick_plans=[obj(x) for x in con.execute('SELECT * FROM blue_tick_plans ORDER BY id').fetchall()]
    con.close()
    return render_template('admin_tariffs.html',
        story_tariffs=get_story_tariffs(), ad_tariffs=get_ad_tariffs(),
        plans=plans, blue_tick_plans=blue_tick_plans)

@app.post('/admin/subscription-plan/save')
@login_required
@role_required('admin')
def admin_subscription_plan_save():
    try:
        plan_id=request.form.get('plan_id', type=int)
        duration=max(1,int(request.form.get('duration_days','1') or 1))
        price=max(0,int(request.form.get('price','0') or 0))
    except ValueError:
        flash('مدت یا قیمت اشتراک نامعتبر است.','danger'); return redirect(url_for('admin_tariffs'))
    name=request.form.get('name','').strip() or f'اشتراک {duration} روزه'
    description=request.form.get('description','').strip()
    active=1 if request.form.get('is_active')=='1' else 0
    con=connect()
    is_trial=0
    if plan_id:
        existing=con.execute('SELECT is_trial FROM subscription_plans WHERE id=?',(plan_id,)).fetchone()
        is_trial=int(existing['is_trial'] or 0) if existing else 0
    if is_trial:
        # پلن آزمایشی همیشه ۷ روز و رایگان باقی می‌ماند.
        duration=7; price=0
    if plan_id:
        con.execute('UPDATE subscription_plans SET name=?,duration_days=?,price=?,description=?,is_active=? WHERE id=?',
                    (name,duration,price,description,active,plan_id))
    else:
        con.execute('INSERT INTO subscription_plans(name,duration_days,price,description,is_active,is_trial) VALUES(?,?,?,?,?,0)',
                    (name,duration,price,description,active))
    con.commit(); con.close()
    flash('پلن اشتراک ذخیره شد.','success')
    return redirect(url_for('admin_tariffs'))

@app.post('/admin/subscription-plan/<int:plan_id>/delete')
@login_required
@role_required('admin')
def admin_subscription_plan_delete(plan_id):
    con=connect()
    con.execute('UPDATE subscription_plans SET is_active=0 WHERE id=?',(plan_id,))
    con.commit(); con.close()
    flash('اشتراک غیرفعال شد.','success')
    return redirect(url_for('admin_tariffs'))

@app.route('/admin-panel')
@login_required
@role_required('admin')
def admin_panel():
    con=connect()
    users=[obj(x) for x in con.execute('SELECT * FROM users ORDER BY id DESC').fetchall()]
    listings=[obj(x) for x in con.execute('SELECT * FROM listings ORDER BY id DESC').fetchall()]
    bookings=[obj(x) for x in con.execute('SELECT b.*,l.title,u.full_name guest_name FROM bookings b JOIN listings l ON l.id=b.listing_id JOIN users u ON u.id=b.user_id ORDER BY b.id DESC').fetchall()]
    ads=[obj(x) for x in con.execute('SELECT a.*,l.title,u.full_name host_name FROM ads a JOIN listings l ON l.id=a.listing_id JOIN users u ON u.id=a.host_id ORDER BY a.id DESC').fetchall()]
    stories=[obj(x) for x in con.execute('SELECT s.*,l.title listing_title,u.full_name host_name FROM stories s LEFT JOIN listings l ON l.id=s.listing_id JOIN users u ON u.id=s.host_id ORDER BY s.id DESC').fetchall()]
    home_slides=[obj(x) for x in con.execute('SELECT * FROM home_slides ORDER BY sort_order,id').fetchall()]
    published_listings=[obj(x) for x in con.execute('SELECT id,title FROM listings WHERE status="published" ORDER BY title').fetchall()]
    tickets=[obj(x) for x in con.execute('SELECT t.*,u.full_name FROM support_tickets t JOIN users u ON u.id=t.user_id ORDER BY t.id DESC').fetchall()]
    payments=[obj(x) for x in con.execute('SELECT p.*,u.full_name FROM payments p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 100').fetchall()]
    coupons=[obj(x) for x in con.execute('SELECT * FROM coupons ORDER BY id DESC').fetchall()]
    reports=[obj(x) for x in con.execute('SELECT r.*,l.title listing_title,u.full_name reporter_name FROM ad_reports r JOIN listings l ON l.id=r.listing_id JOIN users u ON u.id=r.reporter_id ORDER BY r.id DESC').fetchall()]
    categories=[obj(x) for x in con.execute('SELECT * FROM categories ORDER BY parent_id,sort_order,id').fetchall()]
    cities=[obj(x) for x in con.execute('SELECT * FROM cities ORDER BY province,sort_order,id').fetchall()]
    content={r['key']:r['body'] for r in con.execute("SELECT key,body FROM content_pages WHERE key IN ('terms','privacy')").fetchall()}
    app_settings={r['key']:r['value'] for r in con.execute("SELECT key,value FROM app_settings").fetchall()}
    revenue=con.execute('SELECT COALESCE(SUM(amount),0) r FROM payments WHERE status IN ("paid","committed")').fetchone()['r']
    booking_revenue=con.execute('SELECT COALESCE(SUM(total_price),0) r FROM bookings WHERE status IN ("confirmed","completed")').fetchone()['r']
    counts={k:con.execute(q).fetchone()['c'] for k,q in {
        'pending_listings':"SELECT COUNT(*) c FROM listings WHERE workflow_status='pending_review' OR status='pending'",
        'revision_listings':"SELECT COUNT(*) c FROM listings WHERE workflow_status='needs_revision'",
        'pending_ads':"SELECT COUNT(*) c FROM ads WHERE status='pending'",
        'pending_bookings':"SELECT COUNT(*) c FROM bookings WHERE status='pending'",
        'pending_stories':"SELECT COUNT(*) c FROM stories WHERE status='pending'",
        'open_tickets':"SELECT COUNT(*) c FROM support_tickets WHERE status!='closed'",
        'pending_reports':"SELECT COUNT(*) c FROM ad_reports WHERE status='pending'",
    }.items()}
    today_users=con.execute("SELECT COUNT(*) c FROM users WHERE date(created_at)=date('now')").fetchone()['c']
    today_listings=con.execute("SELECT COUNT(*) c FROM listings WHERE date(created_at)=date('now')").fetchone()['c']
    today_bookings=con.execute("SELECT COUNT(*) c FROM bookings WHERE date(created_at)=date('now')").fetchone()['c']
    con.close()
    for l in listings: l.host=user_by_id(l.host_id)
    return render_template('admin_panel.html',users=users,listings=listings,bookings=bookings,ads=ads,stories=stories,home_slides=home_slides,
        published_listings=published_listings,revenue=revenue,booking_revenue=booking_revenue,counts=counts,tickets=tickets,reports=reports,
        categories=categories,cities=cities,content=content,payments=payments,coupons=coupons,today_users=today_users,today_listings=today_listings,today_bookings=today_bookings,app_settings=app_settings)

@app.route('/admin/listing/<int:listing_id>')
@login_required
@role_required('admin')
def admin_listing_detail(listing_id):
    con=connect()
    row=con.execute('SELECT * FROM listings WHERE id=?',(listing_id,)).fetchone()
    if not row:
        con.close(); abort(404)
    listing=obj(row)
    images=[obj(x) for x in con.execute('SELECT * FROM images WHERE listing_id=? ORDER BY sort_order,id',(listing_id,)).fetchall()]
    host_row=con.execute('SELECT * FROM users WHERE id=?',(listing.host_id,)).fetchone()
    history=[obj(x) for x in con.execute('SELECT h.*,u.full_name actor_name FROM listing_status_history h LEFT JOIN users u ON u.id=h.actor_id WHERE h.listing_id=? ORDER BY h.id DESC',(listing_id,)).fetchall()]
    con.close()
    listing.images=images
    listing.host=obj(host_row) if host_row else None
    try:
        listing.details=json.loads(getattr(listing,'details_json','') or '{}')
    except Exception:
        listing.details={}
    detail_labels={
        'brand':'برند','model':'مدل','year':'سال','gearbox':'گیربکس','fuel':'سوخت','kilometers':'کارکرد','insurance':'بیمه',
        'mileage_limit':'محدودیت کیلومتر','driver_conditions':'شرایط راننده','device_type':'نوع دستگاه','storage':'حافظه/فضا',
        'included_items':'لوازم همراه','item_condition':'وضعیت','quantity':'تعداد','equipment_type':'نوع تجهیزات','camera_type':'نوع دوربین',
        'megapixels':'رزولوشن / مگاپیکسل','lens':'لنز / فاصله کانونی','camera_storage':'حافظه دوربین','camera_accessories':'لوازم دوربین',
        'stay_type':'نوع اقامتگاه','stay_bedrooms':'تعداد خواب','stay_bathrooms':'تعداد حمام','stay_capacity':'ظرفیت','stay_amenities':'امکانات اقامتگاه',
        'stay_rules':'قوانین اقامت','rules':'قوانین'
    }
    listing.detail_items=[(detail_labels.get(k,k),v) for k,v in listing.details.items() if v not in (None,'')]
    return render_template('admin_listing_detail.html', listing=listing, history=history)


@app.post('/admin/listing/<int:listing_id>/<action>')
@login_required
@role_required('admin')
def admin_listing_action(listing_id, action):
    if action not in ('approve','reject','revision','suspend','expire'): abort(400)
    mapping={
        'approve':('published','approved'),
        'reject':('rejected','rejected'),
        'revision':('rejected','needs_revision'),
        'suspend':('suspended','needs_revision'),
        'expire':('suspended','expired')
    }
    status,workflow=mapping[action]
    reason=request.form.get('reason','').strip()
    if action in ('reject','revision') and not reason:
        flash('برای رد یا درخواست اصلاح، دلیل را وارد کنید.','danger'); return redirect(url_for('admin_listing_detail',listing_id=listing_id))
    con=connect(); r=con.execute('SELECT host_id,title FROM listings WHERE id=?',(listing_id,)).fetchone()
    if not r: con.close(); abort(404)
    con.execute('UPDATE listings SET status=?, workflow_status=?, rejection_reason=? WHERE id=?',(status,workflow,reason or None,listing_id))
    con.execute('INSERT INTO listing_status_history(listing_id,status,reason,actor_id) VALUES(?,?,?,?)',(listing_id,workflow,reason,session['user_id']))
    con.commit(); con.close()
    labels={'approved':'تأیید شده','rejected':'رد شده','needs_revision':'نیازمند اصلاح','expired':'منقضی شده'}
    msg=f"«{r['title']}» اکنون در وضعیت {labels.get(workflow,workflow)} قرار دارد."
    if reason: msg += f' دلیل: {reason}'
    notify(r['host_id'],'وضعیت آگهی تغییر کرد',msg)
    flash('وضعیت آگهی به‌روزرسانی شد.','success'); return redirect(url_for('admin_panel'))

@app.post('/admin/booking/<int:booking_id>/<action>')
@login_required
@role_required('admin')
def admin_booking_action(booking_id, action):
    if action not in ('approve','reject'): abort(400)
    status='confirmed' if action=='approve' else 'rejected'; con=connect(); r=con.execute('SELECT b.user_id,l.title FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE b.id=?',(booking_id,)).fetchone();
    if not r: con.close(); abort(404)
    con.execute('UPDATE bookings SET status=? WHERE id=?',(status,booking_id)); con.commit(); con.close(); notify(r['user_id'],'وضعیت رزرو تغییر کرد',f"رزرو «{r['title']}» {('تأیید' if status=='confirmed' else 'رد')} شد."); flash('رزرو به‌روزرسانی شد.','success'); return redirect(url_for('admin_panel'))

@app.post('/admin/ad/<int:ad_id>/<action>')
@login_required
@role_required('admin')
def admin_ad_action(ad_id, action):
    if action not in ('approve','reject'): abort(400)
    con=connect(); r=con.execute('SELECT host_id,duration_hours FROM ads WHERE id=?',(ad_id,)).fetchone()
    if not r: con.close(); abort(404)
    status='active' if action=='approve' else 'rejected'
    now=datetime.now()
    duration=int(r['duration_hours'] or 1)
    ad_tariffs = get_ad_tariffs(); ad_prices = tariff_map(ad_tariffs); ad_labels = tariff_labels(ad_tariffs)
    if duration not in ad_prices: duration=int(ad_tariffs[0]['duration'])
    end=now+timedelta(hours=duration)
    con.execute('UPDATE ads SET status=?, starts_at=?, ends_at=? WHERE id=?',(status,now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),ad_id))
    con.commit(); con.close()
    notify(r['host_id'],'وضعیت تبلیغ تغییر کرد', 'تبلیغ شما فعال شد.' if status=='active' else 'تبلیغ شما رد شد.')
    return redirect(url_for('admin_panel'))

@app.route('/admin/users/<int:user_id>/<action>', methods=['POST'])
@login_required
@role_required('admin')
def admin_user_action(user_id, action):
    if action not in ('activate','deactivate'): abort(400)
    con=connect(); con.execute('UPDATE users SET is_active=? WHERE id=?',(1 if action=='activate' else 0,user_id)); con.commit(); con.close(); flash('وضعیت کاربر تغییر کرد.','success'); return redirect(url_for('admin_panel'))

def jalali_to_gregorian(jy, jm, jd):
    jy = int(jy); jm = int(jm); jd = int(jd)
    gy = 621 if jy <= 979 else 1600
    jy2 = jy if jy <= 979 else jy - 979
    days = 365*jy2 + (jy2//33)*8 + ((jy2%33)+3)//4 + 78 + jd + ((jm-1)*31 if jm < 7 else (jm-7)*30 + 186)
    gy += 400*(days//146097); days %= 146097
    if days > 36524:
        gy += 100*((days-1)//36524); days = (days-1)%36524
        if days >= 365: days += 1
    gy += 4*(days//1461); days %= 1461
    if days > 365:
        gy += (days-1)//365; days=(days-1)%365
    gd=days+1
    sal=[0,31,29 if (gy%4==0 and (gy%100!=0 or gy%400==0)) else 28,31,30,31,30,31,31,30,31,30,31]
    gm=1
    while gd>sal[gm]: gd-=sal[gm]; gm+=1
    return datetime(gy,gm,gd)

def gregorian_to_jalali(gy, gm, gd):
    g_d_m=[0,31,59,90,120,151,181,212,243,273,304,334]
    gy2=gy+1 if gm>2 else gy
    days=355666+365*gy+((gy+3)//4)-((gy+99)//100)+((gy+399)//400)+gd+g_d_m[gm-1]
    jy=-1595+33*(days//12053); days%=12053; jy+=4*(days//1461); days%=1461
    if days>365: jy+=(days-1)//365; days=(days-1)%365
    jm=1+(days//31) if days<186 else 7+((days-186)//30)
    jd=1+(days%31 if days<186 else (days-186)%30)
    return f'{jy:04d}/{jm:02d}/{jd:02d}'

def parse_jalali_date(value):
    value=(value or '').strip().replace('-','/')
    value=value.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','0123456789'+'0123456789'))
    m=re.fullmatch(r'(\d{4})/(\d{1,2})/(\d{1,2})',value)
    if not m: return None
    try: return jalali_to_gregorian(*map(int,m.groups()))
    except Exception: return None

def listing_is_available(con, listing_id, check_in, check_out):
    overlap=con.execute('SELECT 1 FROM bookings WHERE listing_id=? AND status IN ("pending","confirmed") AND check_in < ? AND check_out > ? LIMIT 1',(listing_id,check_out.isoformat(),check_in.isoformat())).fetchone()
    manual=con.execute('SELECT 1 FROM listing_availability WHERE listing_id=? AND date>=date(?) AND date<date(?) AND status IN ("blocked","pending") LIMIT 1',(listing_id,check_in.isoformat(),check_out.isoformat())).fetchone()
    return not (overlap or manual)

@app.route('/search')
def search():
    city=request.args.get('city','').strip(); area=request.args.get('area','').strip(); category=request.args.get('category','').strip()
    guests=request.args.get('guests',''); min_price=request.args.get('min_price',''); max_price=request.args.get('max_price','')
    min_rating=request.args.get('min_rating','').strip(); text_query=request.args.get('q','').strip()
    sort=request.args.get('sort','best').strip() or 'best'
    check_in_j=request.args.get('check_in','').strip(); check_out_j=request.args.get('check_out','').strip()
    check_in=parse_jalali_date(check_in_j); check_out=parse_jalali_date(check_out_j)
    verified=request.args.get('verified')=='1'; instant=request.args.get('instant_book')=='1'
    pets=request.args.get('pets')=='1'; party=request.args.get('party')=='1'; parking=request.args.get('parking')=='1'; pool=request.args.get('pool')=='1'
    min_bedrooms=request.args.get('min_bedrooms','').strip()
    detail_keys=['stay_type','stay_bedrooms_min','stay_bathrooms_min','stay_capacity_min','stay_amenities','brand','model','year_min','gearbox','fuel','kilometers_max','insurance','device_type','equipment_type','storage','item_condition','quantity_min','included_items','camera_type','megapixels_min','lens','camera_storage','camera_accessories']
    detail_filters={k:request.args.get(k,'').strip() for k in detail_keys}
    q='SELECT * FROM listings WHERE status="published"'; params=[]
    if city: q+=' AND city LIKE ?'; params.append('%'+city+'%')
    if area: q+=' AND area LIKE ?'; params.append('%'+area+'%')
    if category and category!='all': q+=' AND category=?'; params.append(category)
    if guests.isdigit(): q+=' AND max_guests>=?'; params.append(int(guests))
    if min_bedrooms.isdigit(): q+=' AND bedrooms>=?'; params.append(int(min_bedrooms))
    if min_price.isdigit(): q+=' AND price_per_night>=?'; params.append(int(min_price))
    if max_price.isdigit(): q+=' AND price_per_night<=?'; params.append(int(max_price))
    if min_rating:
        try: q+=' AND rating>=?'; params.append(float(min_rating))
        except ValueError: pass
    if verified: q+=' AND is_verified=1'
    if instant: q+=' AND instant_book=1'
    if pets: q+=' AND pets_allowed=1'
    if party: q+=' AND party_allowed=1'
    if parking: q+=' AND parking=1'
    if pool: q+=' AND pool=1'
    if text_query:
        words=[w for w in re.split(r'\s+',text_query) if len(w)>1]
        for word in words[:8]:
            q+=' AND (title LIKE ? OR description LIKE ? OR amenities LIKE ? OR house_rules LIKE ? OR city LIKE ? OR area LIKE ?)'; term='%'+word+'%'; params.extend([term]*6)
    order={'cheap':'price_per_night ASC','expensive':'price_per_night DESC','newest':'id DESC','rating':'rating DESC','popular':'view_count DESC','best':'featured DESC,rating DESC,review_count DESC,id DESC'}.get(sort,'featured DESC,rating DESC,review_count DESC,id DESC')
    con=connect(); rows=con.execute(q+' ORDER BY '+order,params).fetchall()
    cities=[r['name'] for r in con.execute("SELECT DISTINCT name FROM cities WHERE is_active=1 ORDER BY sort_order,id").fetchall()]
    categories=[obj(r) for r in con.execute("SELECT * FROM categories WHERE is_active=1 AND parent_id IS NULL ORDER BY sort_order,id").fetchall()]
    con.close()
    # هر فیلتر اختصاصی روی details_json همان آگهی اعمال می‌شود.
    def contains(key):
        return detail_filters.get(key,'').lower()

    filtered=[]
    for r in rows:
        x=attach_listing_data(obj(r))
        try: details=json.loads(getattr(x,'details_json','') or '{}')
        except Exception: details={}

        # فقط فیلترهای دسته فعلی
        if category in ('stay','villa','ecotourism','suite','apartment','furnished','cabin'):
            checks=[('stay_type','stay_type'),('stay_amenities','stay_amenities')]
            if any(contains(a) and contains(a) not in str(details.get(b,'')).lower() for a,b in checks): continue
            for key in ('stay_bedrooms_min','stay_bathrooms_min','stay_capacity_min'):
                val=detail_filters[key]
                if val:
                    try:
                        source={'stay_bedrooms_min':'stay_bedrooms','stay_bathrooms_min':'stay_bathrooms','stay_capacity_min':'stay_capacity'}[key]
                        if float(details.get(source,0) or 0)<float(val): continue
                    except (ValueError,TypeError): continue
        elif category=='car':
            for key in ('brand','model','gearbox','fuel','insurance'):
                if contains(key) and contains(key) not in str(details.get(key,'')).lower(): break
            else:
                try:
                    if detail_filters['year_min'] and float(details.get('year',0) or 0)<float(detail_filters['year_min']): continue
                    if detail_filters['kilometers_max'] and float(details.get('kilometers',0) or 0)>float(detail_filters['kilometers_max']): continue
                except (ValueError,TypeError): pass
                filtered.append(x); continue
            continue
        elif category in ('equipment','gaming','events','camp','sport'):
            for key in ('brand','model','device_type','equipment_type','storage','item_condition','included_items'):
                if contains(key) and contains(key) not in str(details.get(key,'')).lower(): break
            else:
                if detail_filters['quantity_min']:
                    try:
                        if float(details.get('quantity',0) or 0)<float(detail_filters['quantity_min']): continue
                    except (ValueError,TypeError): continue
                filtered.append(x); continue
            continue
        elif category=='camera':
            for key in ('brand','model','camera_type','lens','camera_storage','camera_accessories'):
                if contains(key) and contains(key) not in str(details.get(key,'')).lower(): break
            else:
                if detail_filters['megapixels_min']:
                    try:
                        if float(details.get('megapixels',0) or 0)<float(detail_filters['megapixels_min']): continue
                    except (ValueError,TypeError): continue
                filtered.append(x); continue
            continue
        filtered.append(x)

    if check_in and check_out and check_out>check_in:
        con=connect(); filtered=[x for x in filtered if listing_is_available(con,x.id,check_in,check_out)]; con.close()
    return render_template('search.html',listings=filtered,city=city,area=area,category=category,guests=guests,
        min_price=min_price,max_price=max_price,min_rating=min_rating,text_query=text_query,cities=cities,
        categories=categories,detail_filters=detail_filters,sort=sort,check_in_j=check_in_j,check_out_j=check_out_j,
        verified=verified,instant=instant,pets=pets,party=party,parking=parking,pool=pool,min_bedrooms=min_bedrooms)

@app.post('/listing/<int:listing_id>/contact-click')
def listing_contact_click(listing_id):
    """ثبت کلیک روی نمایش شماره تماس آگهی برای Analytics.
    این مسیر عمداً نیاز به ورود ندارد چون نمایش شماره می‌تواند برای مهمان هم فعال باشد.
    """
    con = connect()
    row = con.execute('SELECT id FROM listings WHERE id=?', (listing_id,)).fetchone()
    if not row:
        con.close()
        return jsonify(ok=False, message='آگهی پیدا نشد.'), 404
    con.execute(
        'UPDATE listings SET contact_clicks=COALESCE(contact_clicks,0)+1 WHERE id=?',
        (listing_id,)
    )
    con.commit()
    con.close()
    return jsonify(ok=True)

@app.route('/listing/<int:listing_id>')
def listing_detail(listing_id):
    con=connect()
    r=con.execute('SELECT * FROM listings WHERE id=?',(listing_id,)).fetchone()
    revs=con.execute('SELECT * FROM reviews WHERE listing_id=? ORDER BY id DESC',(listing_id,)).fetchall()
    if not r: con.close(); abort(404)
    con.execute('UPDATE listings SET view_count=COALESCE(view_count,0)+1 WHERE id=?',(listing_id,))
    con.execute('INSERT INTO host_stats(user_id,total_views,updated_at) VALUES(?,1,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET total_views=total_views+1,updated_at=CURRENT_TIMESTAMP',(r['host_id'],))
    listing=attach_listing_data(obj(r)); listing.host=user_by_id(listing.host_id)
    try: listing.details=json.loads(getattr(listing,'details_json','') or '{}')
    except Exception: listing.details={}
    cat=con.execute('SELECT name FROM categories WHERE slug=? AND is_active=1 LIMIT 1',(listing.category,)).fetchone()
    listing.category_name=cat['name'] if cat else (listing.category or 'آگهی')
    host_success=con.execute('SELECT COUNT(*) c FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE l.host_id=? AND b.status IN ("confirmed","completed")',(listing.host_id,)).fetchone()['c']
    host_rating=con.execute('SELECT COALESCE(AVG(r.rating),0) a FROM reviews r JOIN listings l ON l.id=r.listing_id WHERE l.host_id=?',(listing.host_id,)).fetchone()['a']
    host_listing_count=con.execute('SELECT COUNT(*) c FROM listings WHERE host_id=? AND status="published"',(listing.host_id,)).fetchone()['c']
    con.close(); listing.host_successful_rentals=host_success; listing.host_rating=round(host_rating or 0,1); listing.host_listing_count=host_listing_count
    reviews=[]
    for rr in revs: x=obj(rr); x.user=user_by_id(x.user_id); reviews.append(x)
    fav=False; price_drop=False
    if session.get('user_id'):
        con=connect()
        fav_row=con.execute('SELECT saved_price FROM favorites WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id)).fetchone()
        fav=bool(fav_row)
        if fav_row and int(fav_row['saved_price'] or 0)>int(listing.price_per_night or 0):
            price_drop=True
            con.execute('UPDATE favorites SET saved_price=? WHERE user_id=? AND listing_id=?',(int(listing.price_per_night or 0),session['user_id'],listing_id))
            con.execute('UPDATE price_alerts SET last_price=? WHERE user_id=? AND listing_id=?',(int(listing.price_per_night or 0),session['user_id'],listing_id))
            con.execute('INSERT INTO notifications(user_id,title,body) VALUES(?,?,?)',(session['user_id'],'💰 کاهش قیمت آگهی مورد علاقه',f'قیمت «{listing.title}» کاهش پیدا کرده است.'))
            con.commit()
        con.close()
    listing.host_trust_score=trust_score(listing.host_id)
    compare_selected=False
    if session.get('user_id'):
        con=connect(); compare_selected=bool(con.execute('SELECT 1 FROM listing_comparisons WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id)).fetchone()); con.close()
    return render_template('listing_detail.html', listing=listing, reviews=reviews, is_favorite=fav, price_drop=price_drop, compare_selected=compare_selected)

@app.route('/favorite/<int:listing_id>', methods=['POST'])
@login_required
def toggle_favorite(listing_id):
    con=connect(); exists=con.execute('SELECT 1 FROM favorites WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id)).fetchone();
    if exists:
        con.execute('DELETE FROM favorites WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id))
        con.execute('DELETE FROM price_alerts WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id))
    else:
        row=con.execute('SELECT price_per_night FROM listings WHERE id=?',(listing_id,)).fetchone()
        price=int(row['price_per_night'] or 0) if row else 0
        con.execute('INSERT OR IGNORE INTO favorites(user_id,listing_id,saved_price,price_alert) VALUES(?,?,?,1)',(session['user_id'],listing_id,price))
        con.execute('INSERT OR IGNORE INTO price_alerts(user_id,listing_id,last_price,is_active) VALUES(?,?,?,1)',(session['user_id'],listing_id,price))
    con.commit(); con.close(); return redirect(request.referrer or url_for('listing_detail',listing_id=listing_id))

@app.route('/favorites')
@login_required
def favorites():
    con=connect(); rows=con.execute('SELECT l.* FROM favorites f JOIN listings l ON l.id=f.listing_id WHERE f.user_id=? ORDER BY f.id DESC',(session['user_id'],)).fetchall(); con.close(); return render_template('favorites.html', favorites=[type('F',(),{'listing':attach_listing_data(obj(r))})() for r in rows])

@app.route('/bookings')
@login_required
def bookings():
    con=connect(); rows=con.execute('SELECT b.*, l.title, l.city FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE b.user_id=? ORDER BY b.id DESC',(session['user_id'],)).fetchall(); con.close(); out=[]
    for r in rows:
        x=obj(r); x.listing=type('Listing',(),{'id':r['listing_id'],'title':r['title'],'city':r['city']})(); out.append(x)
    return render_template('bookings.html', bookings=out)

@app.post('/booking/<int:listing_id>')
@login_required
def create_booking(listing_id):
    con=connect(); l=con.execute('SELECT * FROM listings WHERE id=? AND status="published"',(listing_id,)).fetchone()
    if not l: con.close(); abort(404)
    check_in=request.form.get('check_in','').strip(); check_out=request.form.get('check_out','').strip()
    try: guests=max(1,int(request.form.get('guests_count',1) or 1))
    except ValueError: guests=1
    notes=request.form.get('notes','').strip()
    try:
        d_in=datetime.fromisoformat(check_in); d_out=datetime.fromisoformat(check_out)
        if d_out<=d_in: raise ValueError
    except Exception:
        con.close(); flash('بازه زمانی معتبر نیست.','danger'); return redirect(url_for('listing_detail',listing_id=listing_id))
    if guests>int(l['max_guests'] or 1):
        con.close(); flash('تعداد نفرات بیشتر از ظرفیت است.','danger'); return redirect(url_for('listing_detail',listing_id=listing_id))
    overlap=con.execute('SELECT 1 FROM bookings WHERE listing_id=? AND status IN ("pending","confirmed") AND check_in < ? AND check_out > ? LIMIT 1',(listing_id,check_out,check_in)).fetchone()
    manual=con.execute('SELECT 1 FROM listing_availability WHERE listing_id=? AND date>=date(?) AND date<date(?) AND status IN ("blocked","pending") LIMIT 1',(listing_id,check_in,check_out)).fetchone()
    if overlap or manual:
        con.close(); flash('این بازه در دسترس نیست.','warning'); return redirect(url_for('listing_detail',listing_id=listing_id))
    unit=l['rent_unit'] or ('hour' if l['price_per_hour'] else 'day')
    if unit=='hour':
        hours=max(1,int((d_out-d_in).total_seconds()//3600)); total=hours*int(l['price_per_hour'] or l['price_per_night'] or 0)
    else:
        total=0; cur=d_in.date()
        while cur<d_out.date():
            daily=int(l['weekend_price'] or 0) if l['weekend_price'] and cur.weekday() in (3,4) else int(l['price_per_night'] or 0)
            total += daily or int(l['price_per_night'] or 0); cur += timedelta(days=1)
    coupon_code=request.form.get('coupon_code','').strip().upper(); discount=0
    if coupon_code:
        c=con.execute('SELECT * FROM coupons WHERE code=? AND is_active=1',(coupon_code,)).fetchone(); valid=False
        if c:
            try: valid=(not c['expires_at'] or datetime.fromisoformat(c['expires_at'])>=datetime.now()) and (not c['max_uses'] or c['used_count']<c['max_uses'])
            except Exception: valid=False
        if valid: discount=round(total*max(0,min(100,int(c['percent'])))/100); total=max(0,total-discount)
        else: con.close(); flash('کد تخفیف معتبر نیست یا منقضی شده است.','warning'); return redirect(url_for('listing_detail',listing_id=listing_id))
    con.execute('INSERT INTO bookings(user_id,listing_id,check_in,check_out,guests_count,total_price,notes,coupon_code,discount_amount,deposit_amount) VALUES(?,?,?,?,?,?,?,?,?,?)',(session['user_id'],listing_id,check_in,check_out,guests,total,notes,coupon_code,discount,int(l['deposit'] or 0)))
    con.execute('UPDATE listings SET last_booked_at=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),listing_id))
    con.execute('INSERT INTO host_stats(user_id,total_bookings,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET total_bookings=total_bookings+1,updated_at=CURRENT_TIMESTAMP',(l['host_id'],1))
    if coupon_code:
        c=con.execute('SELECT id FROM coupons WHERE code=?',(coupon_code,)).fetchone()
        if c: con.execute('UPDATE coupons SET used_count=used_count+1 WHERE id=?',(c['id'],)); con.execute('INSERT OR IGNORE INTO coupons_usage(coupon_id,user_id,payment_id) VALUES(?,?,NULL)',(c['id'],session['user_id']))
    con.commit(); con.close(); notify(l['host_id'],'درخواست اجاره جدید',f'درخواست جدید برای «{l["title"]}» دریافت شد.')
    flash(f'درخواست رزرو ثبت شد؛ مبلغ نهایی {total:,} تومان است.' + (f' تخفیف: {discount:,} تومان.' if discount else ''),'success'); return redirect(url_for('bookings'))


@app.post('/host/booking/<int:booking_id>/<action>')
@login_required
@role_required('host','admin')
def host_booking_action(booking_id, action):
    if action not in ('approve','reject'): abort(400)
    con=connect(); r=con.execute('SELECT b.*,l.host_id,l.title FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE b.id=?',(booking_id,)).fetchone()
    if not r: con.close(); abort(404)
    current=user_by_id(session['user_id'])
    if current.role!='admin' and r['host_id']!=session['user_id']: con.close(); abort(403)
    if action=='approve':
        conflict=con.execute('SELECT 1 FROM bookings WHERE listing_id=? AND id<>? AND status="confirmed" AND check_in < ? AND check_out > ? LIMIT 1',(r['listing_id'],booking_id,r['check_out'],r['check_in'])).fetchone()
        if conflict: con.close(); flash('این بازه قبلاً رزرو شده است.','warning'); return redirect(url_for('host_panel'))
        status='confirmed'
    else: status='rejected'
    con.execute('UPDATE bookings SET status=? WHERE id=?',(status,booking_id)); con.commit(); con.close()
    notify(r['user_id'],'وضعیت درخواست اجاره',f'درخواست «{r["title"]}» {"تأیید شد" if status=="confirmed" else "رد شد"}.'); return redirect(url_for('host_panel'))


@app.post('/review/<int:listing_id>')
@login_required
def create_review(listing_id):
    rating=max(1,min(5,int(request.form.get('rating',5))))
    comment=request.form.get('comment','').strip()
    def sub_rating(name):
        try: return max(1,min(5,int(request.form.get(name, rating) or rating)))
        except ValueError: return rating
    cleanliness=sub_rating('cleanliness'); location_rating=sub_rating('location_rating')
    amenities_rating=sub_rating('amenities_rating'); host_behavior_rating=sub_rating('host_behavior_rating'); value_rating=sub_rating('value_rating')
    con=connect()
    verified=con.execute('''SELECT 1 FROM bookings WHERE user_id=? AND listing_id=? AND status IN ("confirmed","completed") AND check_out <= datetime("now") LIMIT 1''',(session['user_id'],listing_id)).fetchone()
    if not verified:
        con.close(); flash('ثبت نظر فقط برای مهمانانی امکان‌پذیر است که اقامت تأییدشده داشته‌اند.','warning'); return redirect(url_for('listing_detail',listing_id=listing_id))
    duplicate=con.execute('SELECT 1 FROM reviews WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id)).fetchone()
    if duplicate:
        con.close(); flash('برای این اقامتگاه قبلاً نظر ثبت کرده‌اید.','info'); return redirect(url_for('listing_detail',listing_id=listing_id))
    con.execute('INSERT INTO reviews(user_id,listing_id,rating,comment,is_verified,cleanliness,location_rating,amenities_rating,host_behavior_rating,value_rating) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (session['user_id'],listing_id,rating,comment,1,cleanliness,location_rating,amenities_rating,host_behavior_rating,value_rating))
    avg=con.execute('SELECT AVG(rating) a,COUNT(*) c FROM reviews WHERE listing_id=?',(listing_id,)).fetchone()
    con.execute('UPDATE listings SET rating=?,review_count=? WHERE id=?',(round(avg['a'] or 0,1),avg['c'],listing_id))
    con.commit(); con.close(); flash('نظر تأییدشده شما ثبت شد.','success'); return redirect(url_for('listing_detail',listing_id=listing_id))

@app.post('/host/review/<int:review_id>/reply')
@login_required
@role_required('host','admin')
def host_review_reply(review_id):
    reply=request.form.get('reply','').strip()
    con=connect()
    r=con.execute('SELECT r.id,l.host_id,l.title FROM reviews r JOIN listings l ON l.id=r.listing_id WHERE r.id=?',(review_id,)).fetchone()
    if not r or (user_by_id(session['user_id']).role!='admin' and r['host_id']!=session['user_id']):
        con.close(); abort(403)
    con.execute('UPDATE reviews SET host_reply=? WHERE id=?',(reply,review_id)); con.commit(); con.close()
    flash('پاسخ شما ثبت شد.','success'); return redirect(request.referrer or url_for('host_panel'))

@app.route('/create-listing', methods=['GET','POST'])
@login_required
@role_required('host','admin')
def create_listing():
    u=user_by_id(session['user_id'])
    edit_id=request.args.get('edit','').strip()
    edit_listing=None
    if edit_id.isdigit():
        con=connect(); edit_listing=con.execute('SELECT * FROM listings WHERE id=? AND host_id=?',(int(edit_id),u.id)).fetchone(); con.close()
        if not edit_listing: abort(403)
    if u.role=='host' and not active_subscription(u.id):
        flash('برای ثبت اجاره ابتدا یک اشتراک فعال کنید.','warning'); return redirect(url_for('subscriptions'))
    con=connect()
    cats=con.execute("SELECT * FROM categories WHERE is_active=1 AND parent_id IS NULL ORDER BY sort_order,id").fetchall()
    cities=con.execute("SELECT DISTINCT name FROM cities WHERE is_active=1 ORDER BY sort_order,id").fetchall()
    con.close()
    categories=[obj(x) for x in cats]; city_names=[x['name'] for x in cities]
    if request.method=='POST':
        d={k:request.form.get(k,'').strip() for k in ['title','description','city','area','address','category','amenities','house_rules']}
        try:
            base_price=int(request.form.get('price_per_night',0) or 0); price_hour=int(request.form.get('price_per_hour',0) or 0)
            price_week=int(request.form.get('price_per_week',0) or 0); weekend_price=int(request.form.get('weekend_price',0) or 0)
            deposit=max(0,int(request.form.get('deposit',0) or 0)); guests=max(1,int(request.form.get('max_guests',1) or 1))
            bedrooms=max(0,int(request.form.get('bedrooms',0) or 0)); bathrooms=max(0,int(request.form.get('bathrooms',0) or 0))
        except ValueError:
            flash('مقادیر عددی صحیح نیستند.','danger'); return render_template('create_listing.html',categories=categories,cities=city_names)
        try:
            latitude=float(request.form.get('latitude')) if request.form.get('latitude') else None
            longitude=float(request.form.get('longitude')) if request.form.get('longitude') else None
        except ValueError: latitude=longitude=None
        rent_unit=request.form.get('rent_unit','day').strip() or 'day'
        category_defaults={'stay':'day','car':'day','equipment':'day','gaming':'day','camera':'day','events':'day','camp':'day','sport':'hour'}
        if d['category'] in category_defaults: rent_unit=category_defaults[d['category']] if d['category']!='sport' else (request.form.get('rent_unit','hour').strip() or 'hour')
        contact_phone=request.form.get('contact_phone','').strip() or (u.mobile or '')
        show_contact_phone=1 if request.form.get('show_contact_phone')=='1' else 0
        details={}; keys=['brand','model','year','gearbox','fuel','kilometers','insurance','mileage_limit','driver_conditions','device_type','storage','included_items','item_condition','quantity','equipment_type','camera_type','megapixels','lens','camera_storage','camera_accessories','stay_type','stay_bedrooms','stay_bathrooms','stay_capacity','stay_amenities','stay_rules','surface','beds','parking_spaces','distance_to_center','event_capacity','tent_capacity','sport_type','games_included','rental_delivery_conditions','minimum_rental_duration']
        for key in keys:
            value=request.form.get(key,'').strip()
            if value: details[key]=value
        details['rules']=d['house_rules']
        if d['category']=='stay':
            try:
                guests=max(1,int(request.form.get('stay_capacity') or guests))
            except ValueError: pass
            try:
                bedrooms=max(0,int(request.form.get('stay_bedrooms') or bedrooms))
            except ValueError: pass
            try:
                bathrooms=max(0,int(request.form.get('stay_bathrooms') or bathrooms))
            except ValueError: pass
            if request.form.get('stay_amenities','').strip(): d['amenities']=request.form.get('stay_amenities','').strip()
            if request.form.get('stay_rules','').strip(): d['house_rules']=request.form.get('stay_rules','').strip(); details['rules']=d['house_rules']
        instant_book=1 if request.form.get('instant_book')=='1' else 0
        details['reservation_config']={'category':d['category'],'rent_unit':rent_unit,'base_price':base_price,'hourly_price':price_hour,'weekly_price':price_week,'weekend_price':weekend_price,'deposit':deposit,'instant_book':instant_book,'minimum_duration':request.form.get('minimum_rental_duration','').strip(),'delivery_conditions':request.form.get('rental_delivery_conditions','').strip()}
        pets_allowed=1 if request.form.get('pets_allowed')=='1' else 0
        party_allowed=1 if request.form.get('party_allowed')=='1' else 0
        parking=1 if request.form.get('parking')=='1' else 0
        pool=1 if request.form.get('pool')=='1' else 0
        files=request.files.getlist('images')
        if (not edit_listing) and (not files or not any(f and f.filename for f in files)):
            flash('حداقل یک تصویر انتخاب کنید.','danger'); return render_template('create_listing.html',categories=categories,cities=city_names,edit_listing=edit_listing)
        con=connect()
        if edit_listing:
            listing_id=edit_listing['id']
            con.execute("""UPDATE listings SET title=?,description=?,city=?,area=?,address=?,category=?,price_per_night=?,weekend_price=?,check_in_time=?,check_out_time=?,max_guests=?,bedrooms=?,bathrooms=?,amenities=?,house_rules=?,latitude=?,longitude=?,status='pending',workflow_status='pending_review',rejection_reason=NULL,details_json=?,deposit=?,price_per_hour=?,price_per_week=?,rent_unit=?,contact_phone=?,show_contact_phone=?,instant_book=?,pets_allowed=?,party_allowed=?,parking=?,pool=? WHERE id=?""",
                (d['title'],d['description'],d['city'],d['area'],d['address'],d['category'],base_price,weekend_price,
                 request.form.get('check_in_time','14:00'),request.form.get('check_out_time','12:00'),guests,bedrooms,bathrooms,
                 d['amenities'],d['house_rules'],latitude,longitude,json.dumps(details,ensure_ascii=False),deposit,price_hour,price_week,rent_unit,contact_phone,show_contact_phone,instant_book,pets_allowed,party_allowed,parking,pool,listing_id))
            con.execute("INSERT INTO listing_status_history(listing_id,status,reason,actor_id) VALUES(?,?,?,?)",(listing_id,'pending_review','ویرایش و ارسال مجدد توسط میزبان',u.id))
            con.commit(); con.close()
            count=save_images([f for f in files if f and f.filename],listing_id) if any(f and f.filename for f in files) else 1
        else:
            cur=con.execute("""INSERT INTO listings(host_id,title,description,city,area,address,category,
                price_per_night,weekend_price,check_in_time,check_out_time,max_guests,bedrooms,bathrooms,
                amenities,house_rules,latitude,longitude,status,details_json,deposit,price_per_hour,price_per_week,rent_unit,contact_phone,show_contact_phone,instant_book,pets_allowed,party_allowed,parking,pool)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (u.id,d['title'],d['description'],d['city'],d['area'],d['address'],d['category'],base_price,weekend_price,
                 request.form.get('check_in_time','14:00'),request.form.get('check_out_time','12:00'),guests,bedrooms,bathrooms,
                 d['amenities'],d['house_rules'],latitude,longitude,'pending',json.dumps(details,ensure_ascii=False),deposit,price_hour,price_week,rent_unit,contact_phone,show_contact_phone,instant_book,pets_allowed,party_allowed,parking,pool))
            listing_id=cur.lastrowid
            con.execute("UPDATE listings SET workflow_status='pending_review', rejection_reason=NULL WHERE id=?",(listing_id,))
            con.execute("INSERT INTO listing_status_history(listing_id,status,reason,actor_id) VALUES(?,?,?,?)",(listing_id,'pending_review','ارسال اولیه برای بررسی',u.id))
            con.commit(); con.close()
            count=save_images(files,listing_id)
        if count==0:
            con=connect(); con.execute('DELETE FROM listings WHERE id=?',(listing_id,)); con.commit(); con.close()
            flash('تصویر معتبری آپلود نشد.','danger'); return render_template('create_listing.html',categories=categories,cities=city_names)
        notify(u.id,'آگهی برای بررسی ارسال شد','پس از تأیید مدیر در جاکو نمایش داده می‌شود. استوری به‌صورت خودکار ایجاد نمی‌شود.')
        # همه مدیران از ثبت آگهی جدید مطلع می‌شوند تا بتوانند مشخصات کامل آن را بررسی کنند.
        con=connect()
        admins=con.execute("SELECT id FROM users WHERE role='admin' AND is_active=1").fetchall()
        con.close()
        for admin in admins:
            notify(admin['id'],'آگهی جدید برای بررسی',f'آگهی «{d["title"]}» از طرف {u.full_name} ثبت شده است. برای بررسی جزئیات کامل وارد پنل مدیریت شوید.')
        flash('آگهی با موفقیت ثبت شد و در انتظار تأیید مدیر است. استوری خودکار ساخته نمی‌شود.','success'); return redirect(url_for('host_panel'))
    return render_template('create_listing.html',categories=categories,cities=city_names,edit_listing=edit_listing)


@app.post('/subscriptions/<int:plan_id>/claim-free')
@login_required
@role_required('host')
def claim_free_host_trial(plan_id):
    con=connect()
    plan=con.execute('SELECT * FROM subscription_plans WHERE id=? AND is_active=1 AND is_trial=1',(plan_id,)).fetchone()
    user=con.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
    used=int(user['free_host_trial_used'] or 0)
    already=con.execute("SELECT 1 FROM subscriptions WHERE user_id=? AND transaction_ref='FREE-HOST-TRIAL'",(session['user_id'],)).fetchone()
    if not plan or not user:
        con.close(); flash('پلن رایگان در دسترس نیست.','danger'); return redirect(url_for('subscriptions'))
    if used or already:
        con.close(); flash('پلن ۷ روزه رایگان قبلاً برای این حساب استفاده شده است.','warning'); return redirect(url_for('subscriptions'))
    now=datetime.now(); end=now+timedelta(days=plan['duration_days'])
    con.execute('INSERT INTO subscriptions(user_id,plan_id,starts_at,ends_at,status,transaction_ref) VALUES(?,?,?,?,?,?)',
                (session['user_id'],plan_id,now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),'active','FREE-HOST-TRIAL'))
    con.execute('UPDATE users SET free_host_trial_used=1,host_trial_ends_at=? WHERE id=?',(end.isoformat(timespec='seconds'),session['user_id']))
    con.commit(); con.close()
    flash('پلن ۷ روزه رایگان با موفقیت فعال شد.','success')
    return redirect(url_for('subscriptions'))


@app.route('/blue-tick')
@login_required
@role_required('host','admin')
def blue_tick():
    con=connect()
    plans=[obj(x) for x in con.execute('SELECT * FROM blue_tick_plans WHERE is_active=1 ORDER BY price,duration_days').fetchall()]
    mine=con.execute("""SELECT b.*, p.name plan_name, p.price plan_price, p.duration_days
                        FROM blue_tick_subscriptions b JOIN blue_tick_plans p ON p.id=b.plan_id
                        WHERE b.user_id=? ORDER BY b.id DESC LIMIT 10""",(session['user_id'],)).fetchall()
    con.close()
    return render_template('blue_tick.html', plans=plans, purchases=[obj(x) for x in mine])

@app.post('/blue-tick/<int:plan_id>/buy')
@login_required
@role_required('host','admin')
def buy_blue_tick(plan_id):
    con=connect(); p=con.execute('SELECT * FROM blue_tick_plans WHERE id=? AND is_active=1',(plan_id,)).fetchone(); con.close()
    if not p: abort(404)
    try:
        payment_id,_=create_bazaarpay_payment(session['user_id'],'blue_tick',p['price'],f'تیک آبی {p["name"]}',
                                              {'blue_tick_plan_id':int(plan_id)})
    except Exception as e:
        flash(f'ایجاد پرداخت ناموفق بود: {e}','danger')
        return redirect(url_for('blue_tick'))
    return redirect(url_for('bazaarpay_start',payment_id=payment_id))

@app.route('/subscriptions')
@login_required
@role_required('host','admin')
def subscriptions():
    con=connect()
    plans=[obj(x) for x in con.execute('SELECT * FROM subscription_plans WHERE is_active=1 ORDER BY price,duration_days').fetchall()]
    u=con.execute('SELECT free_host_trial_used FROM users WHERE id=?',(session['user_id'],)).fetchone()
    trial_used=bool(u and u['free_host_trial_used'])
    con.close()
    return render_template('subscriptions.html', plans=plans, subscription=active_subscription(session['user_id']), trial_used=trial_used)

@app.post('/subscriptions/<int:plan_id>/buy')
@login_required
@role_required('host','admin')
def buy_subscription(plan_id):
    con=connect(); p=con.execute('SELECT * FROM subscription_plans WHERE id=? AND is_active=1',(plan_id,)).fetchone(); con.close()
    if not p: abort(404)
    if int(p['is_trial'] or 0) == 1:
        return redirect(url_for('claim_free_host_trial', plan_id=plan_id))
    try:
        payment_id,_=create_bazaarpay_payment(session['user_id'],'subscription',p['price'],f'اشتراک {p["name"]} جاکو',{'plan_id':int(plan_id)})
    except Exception as e:
        flash(f'ایجاد پرداخت ناموفق بود: {e}','danger'); return redirect(url_for('subscriptions'))
    return redirect(url_for('bazaarpay_start',payment_id=payment_id))

@app.route('/ads')
@login_required
@role_required('host','admin')
def ads():
    u=user_by_id(session['user_id']); con=connect(); listings=con.execute('SELECT * FROM listings WHERE host_id=? AND status="published"',(u.id,)).fetchall(); ads=con.execute('SELECT * FROM ads WHERE host_id=? ORDER BY id DESC',(u.id,)).fetchall(); con.close(); ad_tariffs=get_ad_tariffs(); return render_template('ads.html', listings=[obj(x) for x in listings], ads=[obj(x) for x in ads], ad_prices=tariff_map(ad_tariffs), ad_price_labels=tariff_labels(ad_tariffs), ad_tariffs=ad_tariffs)

@app.post('/ads/create')
@login_required
@role_required('host','admin')
def create_ad():
    try: listing_id=int(request.form.get('listing_id')); duration=int(request.form.get('duration_hours',1) or 1)
    except (TypeError,ValueError):
        flash('اطلاعات تبلیغ نامعتبر است.','danger'); return redirect(url_for('ads'))
    placement=request.form.get('placement','featured')
    title=request.form.get('title','').strip()
    ad_tariffs = get_ad_tariffs(); ad_prices = tariff_map(ad_tariffs); ad_labels = tariff_labels(ad_tariffs)
    if duration not in ad_prices: duration=int(ad_tariffs[0]['duration'])
    price=ad_prices[duration]; u=user_by_id(session['user_id'])
    con=connect(); l=con.execute('SELECT id,title FROM listings WHERE id=? AND host_id=? AND status="published"',(listing_id,u.id)).fetchone(); con.close()
    if not l: abort(403)
    ad_file=request.files.get('image')
    ad_filename=''
    if ad_file and ad_file.filename:
        ext=ad_file.filename.rsplit('.',1)[-1].lower() if '.' in ad_file.filename else ''
        if ext not in ALLOWED_IMAGES:
            flash('فرمت تصویر تبلیغ مجاز نیست.','danger'); return redirect(url_for('ads'))
        ad_filename=f'ad_{u.id}_{uuid.uuid4().hex}.{ext}'; ad_file.save(UPLOAD_DIR/ad_filename)
    try:
        payment_id,_=create_bazaarpay_payment(u.id,'advertising',price,f'تبلیغ {l["title"]} - {ad_labels[duration]}',
                                              {'listing_id':listing_id,'placement':placement,'duration_hours':duration,'title':title,'image_filename':ad_filename})
    except Exception as e:
        flash(f'ایجاد پرداخت ناموفق بود: {e}','danger'); return redirect(url_for('ads'))
    return redirect(url_for('bazaarpay_start',payment_id=payment_id))

@app.route('/ad/<int:ad_id>/click')
def ad_click(ad_id):
    con=connect(); r=con.execute('SELECT listing_id FROM ads WHERE id=?',(ad_id,)).fetchone(); con.execute('UPDATE ads SET clicks=clicks+1 WHERE id=?',(ad_id,)); con.commit(); con.close(); return redirect(url_for('listing_detail',listing_id=r['listing_id'])) if r else redirect(url_for('index'))


@app.route('/payment/bazaarpay/<int:payment_id>')
@login_required
def bazaarpay_start(payment_id):
    p=local_payment_row(payment_id)
    if not p or int(p['user_id'])!=int(session['user_id']): abort(404)
    if p['status'] in ('paid','committed'): return redirect(url_for('bazaarpay_callback',payment_id=payment_id))
    if not p['payment_url']: flash('لینک پرداخت بازارپی موجود نیست.','danger'); return redirect(url_for('profile'))
    from urllib.parse import urlencode
    callback_url=url_for('bazaarpay_callback',payment_id=payment_id,_external=True)
    query=urlencode({'token':p['checkout_token'],'redirect_url':callback_url})
    sep='&' if '?' in p['payment_url'] else '?'
    return redirect(p['payment_url']+sep+query)

@app.route('/payment/bazaarpay/callback/<int:payment_id>')
@login_required
def bazaarpay_callback(payment_id):
    p=local_payment_row(payment_id)
    if not p or int(p['user_id'])!=int(session['user_id']): abort(404)
    ok,message=finalize_bazaarpay_payment(payment_id)
    if not ok:
        flash(message,'warning'); return redirect(url_for('payment_result',payment_id=payment_id))
    con=connect(); p=con.execute('SELECT * FROM payments WHERE id=?',(payment_id,)).fetchone()
    try: meta=json.loads(p['metadata'] or '{}')
    except Exception: meta={}
    if p['kind']=='subscription':
        plan_id=int(meta.get('plan_id',0)); plan=con.execute('SELECT * FROM subscription_plans WHERE id=?',(plan_id,)).fetchone()
        if plan and not con.execute("SELECT 1 FROM subscriptions WHERE transaction_ref=?",(p['ref'],)).fetchone():
            now=datetime.now(); end=now+timedelta(days=plan['duration_days'])
            con.execute('INSERT INTO subscriptions(user_id,plan_id,starts_at,ends_at,status,transaction_ref) VALUES(?,?,?,?,?,?)',
                        (p['user_id'],plan_id,now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),'active',p['ref']))
            notify(p['user_id'],'اشتراک فعال شد',f'اشتراک {plan["name"]} با موفقیت خریداری شد.')
    elif p['kind']=='blue_tick':
        plan_id=int(meta.get('blue_tick_plan_id',0))
        plan=con.execute('SELECT * FROM blue_tick_plans WHERE id=? AND is_active=1',(plan_id,)).fetchone()
        if plan and p['status'] in ('paid','committed') and not con.execute("SELECT 1 FROM blue_tick_subscriptions WHERE transaction_ref=?",(p['ref'],)).fetchone():
            now=datetime.now(); end=now+timedelta(days=int(plan['duration_days']))
            con.execute('INSERT INTO blue_tick_subscriptions(user_id,plan_id,starts_at,ends_at,status,transaction_ref) VALUES(?,?,?,?,?,?)',
                        (p['user_id'],plan_id,now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),'active',p['ref']))
            con.execute('UPDATE users SET blue_tick_active=1, blue_tick_ends_at=? WHERE id=?',(end.isoformat(timespec='seconds'),p['user_id']))
            notify(p['user_id'],'تیک آبی فعال شد',f'تیک آبی {plan["name"]} تا {end.strftime("%Y-%m-%d %H:%M")} فعال است.')
    elif p['kind']=='advertising':
        listing_id=int(meta.get('listing_id',0)); duration=int(meta.get('duration_hours',1)); placement=meta.get('placement','featured')
        if listing_id and not con.execute("SELECT 1 FROM ads WHERE payment_ref=?",(p['ref'],)).fetchone():
            con.execute('INSERT INTO ads(listing_id,host_id,placement,price,duration_hours,status,payment_ref,title,image_filename) VALUES(?,?,?,?,?,"pending",?,?,?)',
                        (listing_id,p['user_id'],placement,p['amount'],duration,p['ref'],meta.get('title',''),meta.get('image_filename','')))
            notify(p['user_id'],'درخواست تبلیغ ثبت شد','پرداخت موفق بود و کمپین برای تأیید مدیر ارسال شد.')
    elif p['kind']=='story':
        story_id=int(meta.get('story_id',0)); duration=int(meta.get('duration_hours',1))
        if story_id:
            story=con.execute('SELECT * FROM stories WHERE id=? AND host_id=?',(story_id,p['user_id'])).fetchone()
            if story and story['status']!='rejected':
                # پرداخت فقط اجازه ادامه فرایند را می‌دهد؛ انتشار نهایی پس از تأیید مدیر انجام می‌شود.
                con.execute('UPDATE stories SET status="pending" WHERE id=?',(story_id,))
                notify(p['user_id'],'پرداخت استوری موفق بود','استوری شما پس از بررسی مدیر منتشر می‌شود.')
    con.commit(); con.close()
    flash('پرداخت با موفقیت انجام شد.','success'); return redirect(url_for('payment_result',payment_id=payment_id))

@app.route('/payment/result/<int:payment_id>')
@login_required
def payment_result(payment_id):
    p=local_payment_row(payment_id)
    if not p or int(p['user_id'])!=int(session['user_id']): abort(404)
    target=url_for('subscriptions') if p['kind']=='subscription' else url_for('ads') if p['kind']=='advertising' else url_for('profile')
    return render_template('payment_result.html',payment=obj(p),target=target)

@app.post('/listing/<int:listing_id>/compare')
@login_required
def toggle_compare(listing_id):
    con=connect(); exists=con.execute('SELECT 1 FROM listing_comparisons WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id)).fetchone()
    if exists: con.execute('DELETE FROM listing_comparisons WHERE user_id=? AND listing_id=?',(session['user_id'],listing_id))
    else:
        if con.execute('SELECT COUNT(*) c FROM listing_comparisons WHERE user_id=?',(session['user_id'],)).fetchone()['c']>=4: con.close(); flash('حداکثر ۴ آگهی را می‌توانید مقایسه کنید.','warning'); return redirect(request.referrer or url_for('search'))
        if not con.execute("SELECT 1 FROM listings WHERE id=? AND status='published'",(listing_id,)).fetchone(): con.close(); abort(404)
        con.execute('INSERT OR IGNORE INTO listing_comparisons(user_id,listing_id) VALUES(?,?)',(session['user_id'],listing_id))
    con.commit(); con.close(); return redirect(request.referrer or url_for('search'))

@app.route('/compare')
@login_required
def compare_listings():
    con=connect(); rows=con.execute('SELECT l.* FROM listing_comparisons c JOIN listings l ON l.id=c.listing_id WHERE c.user_id=? ORDER BY c.id DESC',(session['user_id'],)).fetchall(); con.close()
    return render_template('compare.html',listings=[attach_listing_data(obj(r)) for r in rows])

@app.post('/listing/<int:listing_id>/offer')
@login_required
def make_price_offer(listing_id):
    try:
        amount=max(0,int(request.form.get('amount','0').replace(',','')))
    except ValueError:
        amount=0
    message=request.form.get('message','').strip()
    con=connect(); l=con.execute("SELECT id,host_id,title FROM listings WHERE id=? AND status='published'",(listing_id,)).fetchone()
    if not l or amount<=0:
        con.close(); flash('مبلغ پیشنهادی معتبر نیست.','danger'); return redirect(url_for('listing_detail',listing_id=listing_id))
    con.execute('INSERT INTO price_offers(user_id,listing_id,amount,message) VALUES(?,?,?,?)',(session['user_id'],listing_id,amount,message)); con.commit(); con.close()
    notify(l['host_id'],'💰 پیشنهاد قیمت جدید',f'برای «{l["title"]}» پیشنهاد {amount:,} تومانی دریافت کردید.')
    flash('پیشنهاد قیمت برای میزبان ارسال شد.','success'); return redirect(url_for('listing_detail',listing_id=listing_id))

@app.route('/host/offers')
@login_required
@role_required('host','admin')
def host_offers():
    con=connect(); rows=con.execute('SELECT o.*,l.title,u.full_name guest_name FROM price_offers o JOIN listings l ON l.id=o.listing_id JOIN users u ON u.id=o.user_id WHERE l.host_id=? ORDER BY o.id DESC',(session['user_id'],)).fetchall(); con.close(); return render_template('host_offers.html',offers=[obj(r) for r in rows])

@app.post('/host/offer/<int:offer_id>/<action>')
@login_required
@role_required('host','admin')
def host_offer_action(offer_id,action):
    if action not in ('accept','reject'): abort(400)
    con=connect(); o=con.execute('SELECT o.*,l.host_id,l.title FROM price_offers o JOIN listings l ON l.id=o.listing_id WHERE o.id=?',(offer_id,)).fetchone()
    if not o or (user_by_id(session['user_id']).role!='admin' and o['host_id']!=session['user_id']): con.close(); abort(403)
    status='accepted' if action=='accept' else 'rejected'; con.execute('UPDATE price_offers SET status=? WHERE id=?',(status,offer_id)); con.commit(); con.close(); notify(o['user_id'],'پاسخ پیشنهاد قیمت',f'پیشنهاد شما برای «{o["title"]}» {"پذیرفته شد" if status=="accepted" else "رد شد"}.'); return redirect(url_for('host_offers'))

@app.route('/messages')
@login_required
def messages():
    uid=session['user_id']
    con=connect()
    rows=con.execute("""SELECT m.*, u.full_name other_name, l.title listing_title
        FROM messages m
        JOIN users u ON u.id=CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END
        LEFT JOIN listings l ON l.id=m.listing_id
        WHERE m.sender_id=? OR m.receiver_id=?
        ORDER BY m.id DESC""",(uid,uid,uid)).fetchall()
    con.close()
    seen=set(); conversations=[]
    for r in rows:
        other_id=r['receiver_id'] if r['sender_id']==uid else r['sender_id']
        if other_id in seen: continue
        seen.add(other_id); x=obj(r); x.other_id=other_id; conversations.append(x)
    return render_template('messages.html', conversations=conversations)

@app.route('/messages/<int:user_id>', methods=['GET','POST'])
@login_required
def conversation(user_id):
    listing_id=request.args.get('listing_id', type=int)
    # اگر از صفحه یک اقامتگاه وارد چت شده‌ایم، گیرنده باید حتماً میزبان همان اقامتگاه باشد.
    if listing_id:
        con=connect()
        listing=con.execute('SELECT id, host_id, title FROM listings WHERE id=?',(listing_id,)).fetchone()
        con.close()
        if not listing:
            abort(404)
        if int(listing['host_id']) != int(user_id):
            user_id=int(listing['host_id'])
    # ارسال پیام به خود کاربر مجاز نیست؛ به‌جای 400 صفحه را دوستانه مدیریت می‌کنیم.
    if int(user_id) == int(session['user_id']):
        if listing_id:
            flash('این اقامتگاه متعلق به حساب فعلی شماست و نمی‌توانید به خودتان پیام بدهید.', 'warning')
            return redirect(url_for('listing_detail', listing_id=listing_id))
        flash('نمی‌توانید به حساب خودتان پیام ارسال کنید.', 'warning')
        return redirect(url_for('messages'))
    other=user_by_id(user_id)
    if not other:
        abort(404)
    con=connect()
    if request.method=='POST':
        body=request.form.get('body','').strip(); image=request.files.get('image'); filename=''
        if image and image.filename:
            ext=Path(secure_filename(image.filename)).suffix.lower()
            if ext not in {'.jpg','.jpeg','.png','.webp'}: con.close(); flash('فقط تصویر JPG، PNG یا WEBP مجاز است.','warning'); return redirect(url_for('conversation',user_id=user_id,listing_id=listing_id))
            filename=f'msg_{uuid.uuid4().hex}{ext}'; image.save(UPLOAD_DIR/filename)
        if body or filename:
            con.execute('INSERT INTO messages(sender_id,receiver_id,listing_id,body,image_filename) VALUES(?,?,?,?,?)',(session['user_id'],user_id,listing_id,body or '📷 تصویر',filename))
            con.commit()
            notify(user_id,'پیام جدید',f'{user_by_id(session["user_id"]).full_name} برای شما پیام فرستاد.')
        con.close()
        return redirect(url_for('conversation',user_id=user_id,listing_id=listing_id) if listing_id else url_for('conversation',user_id=user_id))
    rows=con.execute('SELECT m.*, u.full_name sender_name FROM messages m JOIN users u ON u.id=m.sender_id WHERE (m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?) ORDER BY m.id ASC',(session['user_id'],user_id,user_id,session['user_id'])).fetchall()
    con.execute('UPDATE messages SET is_read=1 WHERE receiver_id=? AND sender_id=?',(session['user_id'],user_id))
    con.commit(); con.close()
    return render_template('conversation.html', other=other, messages=[obj(x) for x in rows], listing_id=listing_id)

@app.route('/chat/start/<int:user_id>')
@login_required
def start_chat(user_id):
    listing_id=request.args.get('listing_id', type=int)
    # مسیر امن شروع چت از صفحه اقامتگاه: میزبان از خود اقامتگاه استخراج می‌شود.
    if listing_id:
        con=connect()
        listing=con.execute('SELECT id, host_id FROM listings WHERE id=?',(listing_id,)).fetchone()
        con.close()
        if not listing:
            abort(404)
        user_id=int(listing['host_id'])
    return redirect(url_for('conversation', user_id=user_id, listing_id=listing_id) if listing_id else url_for('conversation', user_id=user_id))

@app.post('/booking/<int:booking_id>/cancel')
@login_required
def cancel_booking(booking_id):
    reason=request.form.get('reason','').strip() or 'بدون ذکر دلیل'
    con=connect()
    r=con.execute('SELECT b.*,l.host_id,l.title FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE b.id=?',(booking_id,)).fetchone()
    if not r: con.close(); abort(404)
    current=user_by_id(session['user_id'])
    if r['user_id']!=session['user_id'] and r['host_id']!=session['user_id'] and current.role!='admin':
        con.close(); abort(403)
    if r['status'] not in ('pending','confirmed'):
        con.close(); flash('این رزرو قابل لغو نیست.','warning'); return redirect(url_for('bookings'))
    con.execute('UPDATE bookings SET status=?,notes=? WHERE id=?',('cancelled',reason,booking_id))
    con.commit(); con.close()
    notify(r['host_id'] if r['user_id']==session['user_id'] else r['user_id'],'رزرو لغو شد',f'رزرو «{r["title"]}» لغو شد.')
    flash('رزرو لغو شد.','success'); return redirect(url_for('bookings'))

@app.post('/listing/<int:listing_id>/report')
@login_required
def report_listing(listing_id):
    reason=request.form.get('reason','سایر').strip() or 'سایر'; details=request.form.get('details','').strip()
    con=connect()
    if not con.execute('SELECT 1 FROM listings WHERE id=?',(listing_id,)).fetchone():
        con.close(); abort(404)
    exists=con.execute('SELECT 1 FROM ad_reports WHERE listing_id=? AND reporter_id=? AND status="pending"',(listing_id,session['user_id'])).fetchone()
    if exists:
        con.close(); flash('گزارش قبلی شما در حال بررسی است.','info'); return redirect(url_for('listing_detail',listing_id=listing_id))
    con.execute('INSERT INTO ad_reports(listing_id,reporter_id,reason,details) VALUES(?,?,?,?)',(listing_id,session['user_id'],reason,details))
    con.commit(); con.close(); flash('گزارش شما ثبت شد.','success'); return redirect(url_for('listing_detail',listing_id=listing_id))

@app.route('/listing/<int:listing_id>/availability',methods=['GET','POST'])
@login_required
@role_required('host','admin')
def listing_availability(listing_id):
    u=user_by_id(session['user_id']); con=connect(); l=con.execute('SELECT * FROM listings WHERE id=?',(listing_id,)).fetchone()
    if not l or (u.role!='admin' and l['host_id']!=u.id): con.close(); abort(403)
    if request.method=='POST':
        date_raw=request.form.get('date','').strip(); status=request.form.get('status','blocked')
        dj=parse_jalali_date(date_raw)
        date=dj.strftime('%Y-%m-%d') if dj else date_raw
        if status not in ('available','blocked','pending'): status='blocked'
        try: price=int(request.form.get('price','0') or 0)
        except ValueError: price=0
        con.execute('INSERT INTO listing_availability(listing_id,date,status,price) VALUES(?,?,?,?) ON CONFLICT(listing_id,date) DO UPDATE SET status=excluded.status,price=excluded.price',(listing_id,date,status,price)); con.commit()
    rows=con.execute('SELECT * FROM listing_availability WHERE listing_id=? ORDER BY date DESC',(listing_id,)).fetchall(); con.close()
    av=[]
    for x in rows:
        z=obj(x)
        try:
            g=datetime.fromisoformat(x['date']); z.jalali_date=gregorian_to_jalali(g.year,g.month,g.day)
        except Exception: z.jalali_date=x['date']
        av.append(z)
    return render_template('availability.html',listing=obj(l),availability=av)

@app.post('/push/subscribe')
@login_required
def push_subscribe():
    payload=request.get_json(silent=True) or {}; endpoint=str(payload.get('endpoint','')).strip()
    if not endpoint: return jsonify({'ok':False,'message':'endpoint required'}),400
    con=connect(); con.execute('INSERT INTO push_subscriptions(user_id,endpoint,subscription_json) VALUES(?,?,?) ON CONFLICT(user_id,endpoint) DO UPDATE SET subscription_json=excluded.subscription_json',(session['user_id'],endpoint,json.dumps(payload,ensure_ascii=False))); con.commit(); con.close()
    return jsonify({'ok':True})

@app.post('/admin/categories/save')
@login_required
@role_required('admin')
def admin_category_save():
    con=connect()
    try:
        cid=request.form.get('id',type=int); name=request.form.get('name','').strip(); slug=request.form.get('slug','').strip() or uuid.uuid4().hex[:8]
        icon=request.form.get('icon_svg','').strip(); parent_id=request.form.get('parent_id',type=int) or None
        active=1 if request.form.get('is_active')=='1' else 0; order=max(0,int(request.form.get('sort_order','0') or 0))
        if cid: con.execute('UPDATE categories SET name=?,slug=?,icon_svg=?,parent_id=?,is_active=?,sort_order=? WHERE id=?',(name,slug,icon,parent_id,active,order,cid))
        else: con.execute('INSERT INTO categories(name,slug,icon_svg,parent_id,is_active,sort_order) VALUES(?,?,?,?,?,?)',(name,slug,icon,parent_id,active,order))
        con.commit(); flash('دسته‌بندی ذخیره شد.','success')
    except Exception:
        con.rollback(); flash('ذخیره دسته‌بندی ناموفق بود.','danger')
    con.close(); return redirect(url_for('admin_panel'))

@app.post('/admin/categories/<int:category_id>/delete')
@login_required
@role_required('admin')
def admin_category_delete(category_id):
    con=connect(); con.execute('DELETE FROM categories WHERE id=?',(category_id,)); con.commit(); con.close(); flash('دسته‌بندی حذف شد.','success'); return redirect(url_for('admin_panel'))

@app.post('/admin/cities/save')
@login_required
@role_required('admin')
def admin_city_save():
    name=request.form.get('name','').strip(); province=request.form.get('province','خوزستان').strip() or 'خوزستان'; area=request.form.get('area','').strip()
    if not name: flash('نام شهر را وارد کنید.','danger'); return redirect(url_for('admin_panel'))
    con=connect(); con.execute('INSERT INTO cities(province,name,area) VALUES(?,?,?)',(province,name,area)); con.commit(); con.close(); flash('شهر اضافه شد.','success'); return redirect(url_for('admin_panel'))

@app.post('/admin/cities/<int:city_id>/toggle')
@login_required
@role_required('admin')
def admin_city_toggle(city_id):
    con=connect(); con.execute('UPDATE cities SET is_active=1-is_active WHERE id=?',(city_id,)); con.commit(); con.close(); return redirect(url_for('admin_panel'))

@app.post('/admin/reports/<int:report_id>/<action>')
@login_required
@role_required('admin')
def admin_report_action(report_id,action):
    status={'resolve':'resolved','reject':'rejected','pending':'pending'}.get(action)
    if not status: abort(400)
    con=connect(); con.execute('UPDATE ad_reports SET status=? WHERE id=?',(status,report_id)); con.commit(); con.close(); return redirect(url_for('admin_panel'))

@app.route('/admin/app-settings', methods=['POST'])
@login_required
@role_required('admin')
def admin_app_settings_save():
    fields=['app_name','app_short_name','app_theme_color','push_vapid_public_key',
            'home_banner_enabled','home_banner_title','home_banner_text','home_banner_button',
            'home_banner_target_type','home_banner_listing_id','home_banner_external_url']
    values={}
    for key in fields:
        values[key]=request.form.get(key,'').strip()
    values['home_banner_enabled']='1' if request.form.get('home_banner_enabled')=='1' else '0'
    values['app_name']=values['app_name'] or 'جاکو'
    values['app_short_name']=values['app_short_name'] or values['app_name']
    color=values['app_theme_color'] or '#087d50'
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',color):
        color='#087d50'
    values['app_theme_color']=color
    f=request.files.get('app_icon_file')
    if f and f.filename:
        ext=f.filename.rsplit('.',1)[-1].lower() if '.' in f.filename else ''
        if ext not in ('png','jpg','jpeg','webp'):
            flash('آیکون باید PNG، JPG یا WEBP باشد.','danger')
            return redirect(url_for('admin_panel'))
        filename=f'app_icon_{uuid.uuid4().hex}.{ext}'
        f.save(UPLOAD_DIR/filename)
        values['app_icon']='uploads/'+filename
    banner=request.files.get('home_banner_image')
    if banner and banner.filename:
        ext=banner.filename.rsplit('.',1)[-1].lower() if '.' in banner.filename else ''
        if ext not in ALLOWED_IMAGES:
            flash('فرمت تصویر بنر مجاز نیست.','danger')
            return redirect(url_for('admin_panel'))
        filename=f'home_banner_{uuid.uuid4().hex}.{ext}'
        banner.save(UPLOAD_DIR/filename)
        values['home_banner_image']='uploads/'+filename
    con=connect()
    for key,value in values.items():
        con.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
    con.commit(); con.close()
    flash('تنظیمات اپلیکیشن، PWA و بنر صفحه اصلی ذخیره شد.','success')
    return redirect(url_for('admin_panel'))

@app.post('/admin/content/<key>')
@login_required
@role_required('admin')
def admin_content_save(key):
    if key not in ('terms','privacy'): abort(404)
    title=request.form.get('title','').strip() or ('قوانین و مقررات اپلیکیشن جاکو' if key=='terms' else 'حریم خصوصی اپلیکیشن جاکو')
    body=request.form.get('body','').strip()
    con=connect(); con.execute('INSERT INTO content_pages(key,title,body,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET title=excluded.title,body=excluded.body,updated_at=CURRENT_TIMESTAMP',(key,title,body)); con.commit(); con.close()
    flash('محتوای صفحه ذخیره شد.','success'); return redirect(url_for('admin_panel'))

@app.route('/stories/create', methods=['GET','POST'])
@login_required
@role_required('host','admin')
def create_story():
    u=user_by_id(session['user_id'])
    if request.method=='POST':
        try: duration=int(request.form.get('duration_hours',1) or 1)
        except ValueError: duration=1
        story_tariffs = get_story_tariffs(); story_prices = tariff_map(story_tariffs); story_labels = tariff_labels(story_tariffs)
        if duration not in story_prices: duration=int(story_tariffs[0]['duration'])
        listing_id=request.form.get('listing_id', type=int)
        caption=request.form.get('caption','').strip()
        f=request.files.get('image')
        filename=None
        if f and f.filename:
            ext=f.filename.rsplit('.',1)[-1].lower() if '.' in f.filename else ''
            if ext not in ALLOWED_IMAGES:
                flash('فرمت تصویر مجاز نیست.','danger')
                return redirect(url_for('create_story'))
            filename=f'story_{u.id}_{uuid.uuid4().hex}.{ext}'; f.save(UPLOAD_DIR/filename)
        elif listing_id:
            con=connect(); im=con.execute('SELECT filename FROM images WHERE listing_id=? ORDER BY sort_order,id LIMIT 1',(listing_id,)).fetchone(); con.close()
            filename=im['filename'] if im else None
        if not filename:
            flash('یک تصویر برای استوری انتخاب کنید.','danger')
            return redirect(url_for('create_story'))
        trial_active=bool(u.host_trial_ends_at and datetime.fromisoformat(u.host_trial_ends_at)>=datetime.now() and not u.free_story_used)
        price=0 if trial_active else story_prices[duration]
        # استوری پولی فقط پس از پرداخت فعال می‌شود. تایمر هنگام تأیید/فعال‌سازی شروع می‌شود.
        con=connect()
        cur=con.execute('INSERT INTO stories(host_id,listing_id,image_filename,caption,duration_hours,starts_at,ends_at,status) VALUES(?,?,?,?,?,?,?,?)',
                        (u.id,listing_id,filename,caption,duration,None,None,'pending' if price else 'active'))
        story_id=cur.lastrowid
        if price:
            meta={'story_id':story_id,'duration_hours':duration}
            con.commit(); con.close()
            try:
                payment_id,_=create_bazaarpay_payment(u.id,'story',price,f'استوری {story_labels[duration]} جاکو',meta)
            except Exception as e:
                con=connect(); con.execute('DELETE FROM stories WHERE id=?',(story_id,)); con.commit(); con.close()
                flash(f'ایجاد پرداخت ناموفق بود: {e}','danger')
                return redirect(url_for('create_story'))
            flash('استوری ثبت شد؛ تا پرداخت موفق انجام نشود منتشر نمی‌شود.','success')
            return redirect(url_for('bazaarpay_start',payment_id=payment_id))
        now=datetime.now(); end=now+timedelta(hours=duration)
        con.execute('UPDATE stories SET starts_at=?,ends_at=? WHERE id=?',(now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),story_id))
        con.execute('UPDATE users SET free_story_used=1 WHERE id=?',(u.id,))
        con.commit(); con.close()
        flash('استوری رایگان فعال شد.','success')
        return redirect(url_for('host_panel'))
    con=connect(); listings=con.execute('SELECT * FROM listings WHERE host_id=? AND status="published"',(u.id,)).fetchall(); con.close()
    story_tariffs = get_story_tariffs(); return render_template('create_story.html', listings=[obj(x) for x in listings], story_prices=tariff_map(story_tariffs), story_price_labels=tariff_labels(story_tariffs), story_tariffs=story_tariffs)

@app.post('/admin/story/<int:story_id>/<action>')
@login_required
@role_required('admin')
def admin_story_action(story_id, action):
    if action not in ('approve','reject'): abort(400)
    con=connect(); r=con.execute('SELECT host_id FROM stories WHERE id=?',(story_id,)).fetchone()
    if not r: con.close(); abort(404)
    status='active' if action=='approve' else 'rejected'
    if status=='active':
        r2=con.execute('SELECT duration_hours FROM stories WHERE id=?',(story_id,)).fetchone()
        now=datetime.now()
        end=now+timedelta(hours=int(r2['duration_hours'] or 1))
        con.execute(
            'UPDATE stories SET status=?, starts_at=?, ends_at=? WHERE id=?',
            (status,now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),story_id)
        )
        message='استوری شما تأیید و منتشر شد.'
    else:
        con.execute('UPDATE stories SET status=?, starts_at=NULL, ends_at=NULL WHERE id=?',(status,story_id))
        message='استوری شما رد شد.'
    con.commit()
    con.close()
    notify(r['host_id'],'وضعیت استوری تغییر کرد',message)
    return redirect(url_for('admin_panel'))

@app.post('/listings/<int:listing_id>/promote')
@login_required
@role_required('host','admin')
def promote_listing(listing_id):
    u=user_by_id(session['user_id']); con=connect(); l=con.execute('SELECT id FROM listings WHERE id=? AND host_id=? AND status="published"',(listing_id,u.id)).fetchone()
    if not l: con.close(); abort(403)
    now=datetime.now(); end=now+timedelta(days=7); price=120000; con.execute('INSERT INTO listing_promotions(listing_id,host_id,starts_at,ends_at,price,status) VALUES(?,?,?,?,?,"active")',(listing_id,u.id,now.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),price)); ref='UP-'+uuid.uuid4().hex[:10].upper(); con.execute('INSERT INTO payments(user_id,kind,amount,status,ref) VALUES(?,?,?,?,?)',(u.id,'listing_upgrade',price,'paid',ref)); con.commit(); con.close(); flash('ارتقای ویژه ۷ روزه فعال شد. پرداخت در حالت آزمایشی است.','success'); return redirect(url_for('host_panel'))


@app.route('/support')
@login_required
def support():
    con=connect()
    tickets=[obj(x) for x in con.execute('SELECT * FROM support_tickets WHERE user_id=? ORDER BY id DESC',(session['user_id'],)).fetchall()]
    con.close()
    return render_template('support.html',tickets=tickets)

@app.route('/support/new', methods=['GET','POST'])
@login_required
def support_new():
    if request.method=='POST':
        subject=request.form.get('subject','').strip()
        category=request.form.get('category','عمومی').strip()
        body=request.form.get('body','').strip()
        if not subject or not body:
            flash('عنوان و متن پیام الزامی است.','danger'); return render_template('support_new.html')
        con=connect()
        cur=con.execute('INSERT INTO support_tickets(user_id,subject,category,priority) VALUES(?,?,?,?)',(session['user_id'],subject,category,'normal'))
        tid=cur.lastrowid
        con.execute('INSERT INTO support_messages(ticket_id,sender_id,body) VALUES(?,?,?)',(tid,session['user_id'],body))
        con.commit(); con.close()
        flash('تیکت شما ثبت شد.','success'); return redirect(url_for('support'))
    return render_template('support_new.html')

@app.route('/support/<int:ticket_id>', methods=['GET','POST'])
@login_required
def support_ticket(ticket_id):
    con=connect()
    t=con.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
    if not t or (t['user_id']!=session['user_id'] and user_by_id(session['user_id']).role!='admin'):
        con.close(); abort(404)
    if request.method=='POST':
        body=request.form.get('body','').strip()
        if body:
            con.execute('INSERT INTO support_messages(ticket_id,sender_id,body) VALUES(?,?,?)',(ticket_id,session['user_id'],body))
            con.execute('UPDATE support_tickets SET status="open",updated_at=CURRENT_TIMESTAMP WHERE id=?',(ticket_id,))
            con.commit()
    messages=[obj(x) for x in con.execute('SELECT m.*,u.full_name sender_name,u.role sender_role FROM support_messages m JOIN users u ON u.id=m.sender_id WHERE ticket_id=? ORDER BY m.id',(ticket_id,)).fetchall()]
    con.close()
    return render_template('support_ticket.html',ticket=obj(t),messages=messages)

@app.post('/admin/payout/<int:payout_id>/<action>')
@login_required
@role_required('admin')
def admin_payout_action(payout_id, action):
    if action not in ('approve','reject'): abort(400)
    con=connect()
    r=con.execute('SELECT user_id,amount,status FROM payout_requests WHERE id=?',(payout_id,)).fetchone()
    if not r: con.close(); abort(404)
    status='paid' if action=='approve' else 'rejected'
    con.execute('UPDATE payout_requests SET status=?,processed_at=CURRENT_TIMESTAMP WHERE id=?',(status,payout_id))
    con.commit(); con.close()
    notify(r['user_id'],'وضعیت تسویه',f'درخواست تسویه {r["amount"]:,} تومان '+('پرداخت شد.' if status=='paid' else 'رد شد.'))
    flash('وضعیت تسویه به‌روزرسانی شد.','success'); return redirect(url_for('admin_panel'))

@app.post('/admin/ticket/<int:ticket_id>/<action>')
@login_required
@role_required('admin')
def admin_ticket_action(ticket_id, action):
    if action not in ('close','open','priority'): abort(400)
    con=connect()
    if action=='priority':
        priority=request.form.get('priority','normal')
        if priority not in ('normal','high'): priority='normal'
        con.execute('UPDATE support_tickets SET priority=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(priority,ticket_id))
    else:
        con.execute('UPDATE support_tickets SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',('closed' if action=='close' else 'open',ticket_id))
    con.commit(); con.close(); return redirect(url_for('admin_panel'))

@app.route('/admin/ticket/<int:ticket_id>/reply', methods=['POST'])
@login_required
@role_required('admin')
def admin_ticket_reply(ticket_id):
    body=request.form.get('body','').strip()
    if not body: return redirect(url_for('admin_panel'))
    con=connect()
    t=con.execute('SELECT user_id FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
    if not t: con.close(); abort(404)
    con.execute('INSERT INTO support_messages(ticket_id,sender_id,body) VALUES(?,?,?)',(ticket_id,session['user_id'],body))
    con.execute('UPDATE support_tickets SET status="open",updated_at=CURRENT_TIMESTAMP WHERE id=?',(ticket_id,))
    con.commit(); con.close(); notify(t['user_id'],'پاسخ پشتیبانی','پشتیبانی جاکو به تیکت شما پاسخ داد.')
    return redirect(url_for('admin_panel'))

@app.post('/admin/home-slides/create')
@login_required
@role_required('admin')
def admin_home_slide_create():
    title=request.form.get('title','').strip()
    subtitle=request.form.get('subtitle','').strip()
    city_label=request.form.get('city_label','خوزستان').strip() or 'خوزستان'
    sort_order=int(request.form.get('sort_order',10) or 10)
    is_active=1 if request.form.get('is_active')=='1' else 0
    target_type=request.form.get('target_type','none')
    target_listing_id=request.form.get('target_listing_id', type=int)
    external_url=request.form.get('external_url','').strip()
    f=request.files.get('image')
    filename=None
    if f and f.filename:
        ext=f.filename.rsplit('.',1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_IMAGES:
            flash('فرمت تصویر اسلایدر مجاز نیست.','danger')
            return redirect(url_for('admin_panel'))
        filename=f'home_slide_{uuid.uuid4().hex}.{ext}'
        f.save(UPLOAD_DIR/filename)
    if not title:
        flash('عنوان اسلایدر الزامی است.','danger')
        return redirect(url_for('admin_panel'))
    con=connect()
    con.execute('INSERT INTO home_slides(title,subtitle,city_label,image_filename,sort_order,is_active,target_type,target_listing_id,external_url) VALUES(?,?,?,?,?,?,?,?,?)',
                (title,subtitle,city_label,filename,sort_order,is_active,target_type,target_listing_id,external_url))
    con.commit(); con.close()
    flash('اسلایدر صفحه اصلی اضافه شد.','success')
    return redirect(url_for('admin_panel'))

@app.post('/admin/home-slides/<int:slide_id>/update')
@login_required
@role_required('admin')
def admin_home_slide_update(slide_id):
    title = request.form.get('title', '').strip()
    subtitle = request.form.get('subtitle', '').strip()
    city_label = request.form.get('city_label', 'خوزستان').strip() or 'خوزستان'
    try:
        sort_order = int(request.form.get('sort_order', 1) or 1)
    except ValueError:
        sort_order = 1
    is_active = 1 if request.form.get('is_active') == '1' else 0
    target_type = request.form.get('target_type','none')
    target_listing_id = request.form.get('target_listing_id', type=int)
    external_url = request.form.get('external_url','').strip()
    f = request.files.get('image')

    if not title:
        flash('عنوان اسلایدر الزامی است.', 'danger')
        return redirect(url_for('admin_panel'))

    con = connect()
    old = con.execute('SELECT image_filename FROM home_slides WHERE id=?', (slide_id,)).fetchone()
    if not old:
        con.close()
        abort(404)

    filename = old['image_filename']
    if f and f.filename:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_IMAGES:
            con.close()
            flash('فرمت تصویر اسلایدر مجاز نیست.', 'danger')
            return redirect(url_for('admin_panel'))
        filename = f'home_slide_{uuid.uuid4().hex}.{ext}'
        f.save(UPLOAD_DIR / filename)
        if old['image_filename'] and not str(old['image_filename']).startswith('images/'):
            try:
                old_path = UPLOAD_DIR / Path(old['image_filename']).name
                if old_path.exists():
                    old_path.unlink()
            except Exception:
                pass

    con.execute(
        'UPDATE home_slides SET title=?, subtitle=?, city_label=?, image_filename=?, sort_order=?, is_active=?, target_type=?, target_listing_id=?, external_url=? WHERE id=?',
        (title, subtitle, city_label, filename, sort_order, is_active, target_type, target_listing_id, external_url, slide_id)
    )
    con.commit()
    con.close()
    flash('اسلایدر صفحه اصلی با موفقیت ویرایش شد.', 'success')
    return redirect(url_for('admin_panel'))

@app.post('/admin/home-slides/<int:slide_id>/toggle')
@login_required
@role_required('admin')
def admin_home_slide_toggle(slide_id):
    con=connect()
    r=con.execute('SELECT is_active FROM home_slides WHERE id=?',(slide_id,)).fetchone()
    if not r:
        con.close(); abort(404)
    con.execute('UPDATE home_slides SET is_active=? WHERE id=?',(0 if r['is_active'] else 1,slide_id))
    con.commit(); con.close()
    flash('وضعیت اسلایدر تغییر کرد.','success')
    return redirect(url_for('admin_panel'))

@app.post('/admin/home-slides/<int:slide_id>/delete')
@login_required
@role_required('admin')
def admin_home_slide_delete(slide_id):
    con=connect()
    r=con.execute('SELECT image_filename FROM home_slides WHERE id=?',(slide_id,)).fetchone()
    if not r:
        con.close(); abort(404)
    con.execute('DELETE FROM home_slides WHERE id=?',(slide_id,))
    con.commit(); con.close()
    if r['image_filename'] and not str(r['image_filename']).startswith('images/'):
        try:
            p=UPLOAD_DIR/Path(r['image_filename']).name
            if p.exists(): p.unlink()
        except Exception:
            pass
    flash('اسلایدر حذف شد.','success')
    return redirect(url_for('admin_panel'))

@app.post('/admin/payment-settings/test')
@login_required
@role_required('admin')
def admin_payment_settings_test():
    settings=get_bazaarpay_settings()
    if not settings['token']:
        return jsonify(ok=False,message='توکن بازارپی وارد نشده است.')
    code,data=bazaarpay_request('trace', {'checkout_token':'TEST'}, authenticated=False)
    # trace با توکن ساختگی صرفاً برای بررسی دسترسی API است؛ پاسخ 400/404 هم نشان‌دهنده رسیدن درخواست به API است.
    if code in (200,400,404,422):
        return jsonify(ok=True,message='ارتباط با API بازارپی برقرار است؛ برای تست کامل، یک پرداخت آزمایشی ایجاد کنید.',status=code)
    return jsonify(ok=False,message=str(data.get('detail','اتصال به بازارپی ناموفق بود.')),status=code)

@app.route('/admin/auth-settings', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_auth_settings():
    keys=['sms_provider','sms_cron_enabled','sms_saving_enabled','sms_listing_approve_enabled','sms_listing_reject_enabled','sms_new_listing_admin_enabled','sms_listing_renew_enabled','sms_api_key','sms_otp_template_id','sms_secure_accept_template_id','sms_secure_reject_template_id','sms_secure_new_template_id','sms_secure_complaint_template_id','sms_emergency_login_url']
    if request.method=='POST':
        con=connect()
        for key in keys:
            value=request.form.get(key,'').strip()
            if key.endswith('_enabled'):
                value='1' if request.form.get(key)=='1' else '0'
            con.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
        con.commit(); con.close()
        flash('تنظیمات احراز هویت و پیامک ذخیره شد.','success')
        return redirect(url_for('admin_auth_settings'))
    con=connect(); rows=con.execute("SELECT key,value FROM app_settings WHERE key IN (%s)" % ",".join("?"*len(keys)),keys).fetchall(); con.close()
    settings={r['key']:r['value'] for r in rows}
    return render_template('auth_settings.html', settings=settings)

@app.post('/admin/auth-settings/test-otp')
@login_required
@role_required('admin')
def admin_auth_test_otp():
    mobile=re.sub(r'\D','',request.form.get('mobile',''))
    con=connect(); rows=con.execute("SELECT key,value FROM app_settings WHERE key IN ('sms_api_key','sms_otp_template_id')").fetchall(); con.close()
    s={r['key']:r['value'] for r in rows}
    if not s.get('sms_api_key') or not s.get('sms_otp_template_id'):
        return jsonify(ok=False,message='API Key و شناسه قالب OTP را در تنظیمات وارد کنید.')
    if not re.fullmatch(r'09\d{9}', mobile):
        return jsonify(ok=False,message='شماره موبایل معتبر نیست.')
    code=f"{random.randint(10000,99999)}"
    payload={'mobile':mobile,'templateId':int(s['sms_otp_template_id']),'parameters':[{'name':'CODE','value':code}]}
    req=urllib.request.Request('https://api.sms.ir/v1/send/verify',data=json.dumps(payload).encode('utf-8'),headers={'Content-Type':'application/json','Accept':'application/json','x-api-key':s['sms_api_key']},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as resp:
            raw=resp.read().decode('utf-8',errors='replace'); data=json.loads(raw) if raw else {}
            safe={k:v for k,v in data.items() if str(k).lower() not in ('api_key','apikey','token','authorization')}
            return jsonify(ok=200<=resp.status<300,message='درخواست ارسال OTP ارسال شد.',status=resp.status,response=safe)
    except urllib.error.HTTPError as e:
        raw=e.read().decode('utf-8',errors='replace')
        try: data=json.loads(raw)
        except: data={'detail':raw}
        safe={k:v for k,v in data.items() if str(k).lower() not in ('api_key','apikey','token','authorization')}
        return jsonify(ok=False,message='ارسال OTP ناموفق بود.',status=e.code,response=safe)
    except Exception as e:
        return jsonify(ok=False,message=f'خطای اتصال: {e}')


@app.post('/admin/blue-tick-plan/save')
@login_required
@role_required('admin')
def admin_blue_tick_plan_save():
    try:
        plan_id=request.form.get('plan_id',type=int)
        duration=max(1,int(request.form.get('duration_days','30') or 30))
        price=max(0,int(request.form.get('price','0') or 0))
    except ValueError:
        flash('مدت یا قیمت نامعتبر است.','danger'); return redirect(url_for('admin_tariffs'))
    name=request.form.get('name','تیک آبی').strip() or 'تیک آبی'
    desc=request.form.get('description','').strip()
    active=1 if request.form.get('is_active')=='1' else 0
    con=connect()
    if plan_id:
        con.execute('UPDATE blue_tick_plans SET name=?,duration_days=?,price=?,description=?,is_active=? WHERE id=?',(name,duration,price,desc,active,plan_id))
    else:
        con.execute('INSERT INTO blue_tick_plans(name,duration_days,price,description,is_active) VALUES(?,?,?,?,?)',(name,duration,price,desc,active))
    con.commit(); con.close()
    flash('تعرفه تیک آبی ذخیره شد.','success'); return redirect(url_for('admin_tariffs'))

@app.post('/admin/blue-tick-plan/<int:plan_id>/delete')
@login_required
@role_required('admin')
def admin_blue_tick_plan_delete(plan_id):
    con=connect()
    con.execute('UPDATE blue_tick_plans SET is_active=0 WHERE id=?',(plan_id,))
    con.commit(); con.close()
    flash('پلن تیک آبی غیرفعال شد.','success'); return redirect(url_for('admin_tariffs'))

@app.route('/admin/payment-settings', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_payment_settings():
    if request.method == 'POST':
        current = get_bazaarpay_settings()
        token = request.form.get('bazaarpay_token', '').strip()
        destination = request.form.get('bazaarpay_destination', '').strip() or 'jako'
        try:
            multiplier = max(1, int(request.form.get('bazaarpay_amount_multiplier', '1') or 1))
        except ValueError:
            multiplier = 1
        enabled = '1' if request.form.get('bazaarpay_enabled') == '1' else '0'
        clear_token = request.form.get('clear_bazaarpay_token') == '1'

        if clear_token:
            token = ''
        elif not token:
            token = current['token']

        set_bazaarpay_setting('bazaarpay_token', token)
        set_bazaarpay_setting('bazaarpay_destination', destination)
        set_bazaarpay_setting('bazaarpay_amount_multiplier', multiplier)
        set_bazaarpay_setting('bazaarpay_enabled', enabled)

        if enabled == '1' and not token:
            flash('درگاه ذخیره شد اما فعال نشد؛ برای فعال‌سازی باید توکن بازارپی را وارد کنید.', 'warning')
        else:
            flash('تنظیمات درگاه بازارپی با موفقیت ذخیره شد.', 'success')
        return redirect(url_for('admin_payment_settings'))

    settings = get_bazaarpay_settings()
    return render_template('payment_settings.html', gateway=settings)


# سازگاری با لینک‌های نسخه‌های قبلی
@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users(): return redirect(url_for('admin_panel'))
@app.route('/admin/listings')
@login_required
@role_required('admin')
def admin_listings(): return redirect(url_for('admin_panel'))
@app.route('/admin/bookings')
@login_required
@role_required('admin')
def admin_bookings(): return redirect(url_for('admin_panel'))

def ensure_admin():
    mobile='09055018315'; email='arian.m1520as@gmail.com'; password='123456789'; con=connect(); row=con.execute('SELECT id FROM users WHERE mobile=? OR lower(email)=?',(mobile,email)).fetchone()
    if row: con.execute('UPDATE users SET role="admin",mobile=?,email=?,is_active=1 WHERE id=?',(mobile,email,row['id']))
    else: con.execute('INSERT INTO users(full_name,mobile,email,password_hash,role,accepted_terms,accepted_privacy) VALUES(?,?,?,?,?,?,?)',('مدیر جاکو',mobile,email,generate_password_hash(password),'admin',1,1))
    con.commit(); con.close()
ensure_admin()

@app.post('/admin/users/<int:user_id>/identity')
@login_required
@role_required('admin')
def admin_identity_toggle(user_id):
    con=connect(); con.execute('UPDATE users SET identity_verified=1-identity_verified WHERE id=?',(user_id,)); con.commit(); con.close()
    return redirect(url_for('admin_panel'))

@app.post('/admin/users/<int:user_id>/ownership')
@login_required
@role_required('admin')
def admin_ownership_toggle(user_id):
    con=connect(); con.execute('UPDATE users SET ownership_verified=1-ownership_verified WHERE id=?',(user_id,)); con.commit(); con.close()
    return redirect(url_for('admin_panel'))

@app.post('/admin/coupon/save')
@login_required
@role_required('admin')
def admin_coupon_save():
    code=request.form.get('code','').strip().upper()
    try: percent=max(1,min(100,int(request.form.get('percent','0') or 0))); max_uses=max(0,int(request.form.get('max_uses','0') or 0))
    except ValueError: percent=0; max_uses=0
    expires=request.form.get('expires_at','').strip() or None
    if not code or percent<1:
        flash('کد و درصد تخفیف معتبر نیست.','danger'); return redirect(url_for('admin_panel'))
    con=connect(); con.execute('INSERT INTO coupons(code,percent,max_uses,expires_at,is_active) VALUES(?,?,?,?,1) ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,max_uses=excluded.max_uses,expires_at=excluded.expires_at,is_active=1',(code,percent,max_uses,expires)); con.commit(); con.close()
    flash('کد تخفیف ذخیره شد.','success'); return redirect(url_for('admin_panel'))

@app.post('/admin/coupon/<int:coupon_id>/toggle')
@login_required
@role_required('admin')
def admin_coupon_toggle(coupon_id):
    con=connect(); con.execute('UPDATE coupons SET is_active=1-is_active WHERE id=?',(coupon_id,)); con.commit(); con.close(); return redirect(url_for('admin_panel'))


# ========================= V30: Chat Pro / Analytics / Smart Recommendations =========================
def _ensure_v30_tables():
    con = connect()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS message_reactions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
      reaction TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(message_id,user_id,reaction), FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS listing_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL, user_id INTEGER,
      event_type TEXT NOT NULL, metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS recommendation_feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, listing_id INTEGER NOT NULL,
      action TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(user_id,listing_id,action), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE);
    """)
    con.commit(); con.close()

_ensure_v30_tables()

@app.route('/api/messages/<int:user_id>', methods=['GET','POST'])
@login_required
def messages_api(user_id):
    if int(user_id) == int(session['user_id']): return jsonify({'ok':False,'error':'self_message'}), 400
    other=user_by_id(user_id)
    if not other: return jsonify({'ok':False,'error':'not_found'}), 404
    con=connect()
    if request.method=='POST':
        body=request.form.get('body','').strip(); listing_id=request.form.get('listing_id',type=int)
        reply_to=request.form.get('reply_to_id',type=int); image=request.files.get('image'); filename=''
        if image and image.filename:
            ext=Path(secure_filename(image.filename)).suffix.lower()
            if ext not in {'.jpg','.jpeg','.png','.webp'}:
                con.close(); return jsonify({'ok':False,'error':'invalid_image'}),400
            filename=f'msg_{uuid.uuid4().hex}{ext}'; image.save(UPLOAD_DIR/filename)
        if not body and not filename:
            con.close(); return jsonify({'ok':False,'error':'empty'}),400
        now=datetime.utcnow().isoformat()
        con.execute("""INSERT INTO messages(sender_id,receiver_id,listing_id,body,image_filename,reply_to_id,message_type,delivered_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (session['user_id'],user_id,listing_id,body or '📷 تصویر',filename,reply_to,'image' if filename and not body else 'text',now))
        mid=con.execute('SELECT last_insert_rowid()').fetchone()[0]
        con.commit(); con.close()
        notify(user_id,'پیام جدید',f'{user_by_id(session["user_id"]).full_name} برای شما پیام فرستاد.')
        return jsonify({'ok':True,'id':mid})
    rows=con.execute("""SELECT m.*,u.full_name sender_name FROM messages m JOIN users u ON u.id=m.sender_id
        WHERE (m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?) ORDER BY m.id ASC""",
        (session['user_id'],user_id,user_id,session['user_id'])).fetchall()
    con.execute('UPDATE messages SET is_read=1,read_at=CURRENT_TIMESTAMP WHERE receiver_id=? AND sender_id=?',
                (session['user_id'],user_id))
    con.commit(); con.close()
    return jsonify({'ok':True,'messages':[dict(r) for r in rows]})

@app.route('/message/<int:message_id>/reaction', methods=['POST'])
@login_required
def message_reaction(message_id):
    reaction=(request.form.get('reaction') or '❤️').strip()[:16]
    con=connect()
    msg=con.execute('SELECT id FROM messages WHERE id=? AND (sender_id=? OR receiver_id=?)',
                    (message_id,session['user_id'],session['user_id'])).fetchone()
    if not msg: con.close(); abort(404)
    con.execute('INSERT OR IGNORE INTO message_reactions(message_id,user_id,reaction) VALUES(?,?,?)',
                (message_id,session['user_id'],reaction))
    con.commit(); con.close()
    return jsonify({'ok':True})

@app.route('/messages/search')
@login_required
def messages_search():
    q=request.args.get('q','').strip()
    if not q: return redirect(url_for('messages'))
    con=connect()
    rows=con.execute("""SELECT m.*,u.full_name other_name,l.title listing_title
        FROM messages m JOIN users u ON u.id=CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END
        LEFT JOIN listings l ON l.id=m.listing_id
        WHERE (m.sender_id=? OR m.receiver_id=?) AND m.body LIKE ? ORDER BY m.id DESC LIMIT 50""",
        (session['user_id'],session['user_id'],session['user_id'],f'%{q}%')).fetchall()
    con.close()
    return render_template('message_search.html', rows=[obj(x) for x in rows], query=q)

@app.route('/host/analytics')
@login_required
def host_analytics():
    if session.get('role') not in ['host','admin']: abort(403)
    uid=session['user_id']; con=connect()
    listings=con.execute('SELECT id,title,view_count,contact_clicks,rating,review_count FROM listings WHERE host_id=? ORDER BY view_count DESC',(uid,)).fetchall()
    total_views=con.execute('SELECT COALESCE(SUM(view_count),0) v FROM listings WHERE host_id=?',(uid,)).fetchone()['v']
    total_contacts=con.execute('SELECT COALESCE(SUM(contact_clicks),0) v FROM listings WHERE host_id=?',(uid,)).fetchone()['v']
    total_favorites=con.execute('SELECT COUNT(*) v FROM favorites f JOIN listings l ON l.id=f.listing_id WHERE l.host_id=?',(uid,)).fetchone()['v']
    total_bookings=con.execute('SELECT COUNT(*) v FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE l.host_id=? AND b.status IN ("confirmed","completed")',(uid,)).fetchone()['v']
    revenue=con.execute('SELECT COALESCE(SUM(b.total_price),0) v FROM bookings b JOIN listings l ON l.id=b.listing_id WHERE l.host_id=? AND b.status IN ("confirmed","completed")',(uid,)).fetchone()['v']
    months=[]; monthly=[]; now=datetime.now()
    for n in range(5,-1,-1):
        d=(now.replace(day=1)-timedelta(days=32*n)).replace(day=1); key=d.strftime('%Y-%m')
        row=con.execute("""SELECT COUNT(*) bookings,COALESCE(SUM(total_price),0) revenue FROM bookings b
                           JOIN listings l ON l.id=b.listing_id
                           WHERE l.host_id=? AND b.status IN ("confirmed","completed") AND substr(b.created_at,1,7)=?""",(uid,key)).fetchone()
        months.append(key); monthly.append({'bookings':row['bookings'],'revenue':row['revenue']})
    conv_rate=round((total_bookings/total_views*100),1) if total_views else 0
    con.close()
    return render_template('host_analytics.html', listings=[obj(x) for x in listings],
        total_views=total_views,total_contacts=total_contacts,total_favorites=total_favorites,
        total_bookings=total_bookings,revenue=revenue,months=json.dumps(months,ensure_ascii=False),
        monthly=json.dumps(monthly,ensure_ascii=False),conversion=conv_rate)

def _smart_recommendations(uid, limit=12):
    con=connect()
    prefs=con.execute("""SELECT l.category,l.city,AVG(l.price_per_night) avg_price FROM listings l
                         JOIN favorites f ON f.listing_id=l.id WHERE f.user_id=?
                         GROUP BY l.category,l.city ORDER BY COUNT(*) DESC LIMIT 20""",(uid,)).fetchall()
    last=con.execute("""SELECT l.category,l.city,l.price_per_night FROM bookings b JOIN listings l ON l.id=b.listing_id
                        WHERE b.user_id=? ORDER BY b.id DESC LIMIT 5""",(uid,)).fetchall()
    fav_ids={r['listing_id'] for r in con.execute('SELECT listing_id FROM favorites WHERE user_id=?',(uid,)).fetchall()}
    hidden={r['listing_id'] for r in con.execute("SELECT listing_id FROM recommendation_feedback WHERE user_id=? AND action='hide'",(uid,)).fetchall()}
    rows=con.execute("""SELECT l.*,COALESCE(i.filename,'') image_filename FROM listings l
                        LEFT JOIN images i ON i.listing_id=l.id AND i.sort_order=0
                        WHERE l.status='published' AND l.host_id!=?
                        ORDER BY l.featured DESC,l.rating DESC,l.id DESC LIMIT 250""",(uid,)).fetchall()
    category_counts={}; city_counts={}; target_price=None
    for r in list(prefs)+list(last):
        if r['category']: category_counts[r['category']]=category_counts.get(r['category'],0)+1
        if r['city']: city_counts[r['city']]=city_counts.get(r['city'],0)+1
    prices=[r['avg_price'] for r in prefs if r['avg_price']]
    if prices: target_price=sum(prices)/len(prices)
    if target_price is None and last: target_price=sum((r['price_per_night'] or 0) for r in last)/len(last)
    scored=[]
    for r in rows:
        if r['id'] in fav_ids or r['id'] in hidden: continue
        score=0
        if r['category'] in category_counts: score+=35+min(10,category_counts[r['category']]*3)
        if r['city'] in city_counts: score+=30+min(10,city_counts[r['city']]*3)
        if target_price and r['price_per_night']:
            diff=abs(r['price_per_night']-target_price)/max(target_price,1); score+=max(0,20-diff*20)
        score+=min(10,float(r['rating'] or 0)*2); score+=5 if r['is_verified'] else 0
        score+=3 if r['instant_book'] else 0
        scored.append((score,r))
    scored.sort(key=lambda x:(x[0],x[1]['rating'] or 0,x[1]['id']),reverse=True)
    con.close(); return [obj(r) for _,r in scored[:limit]]

@app.route('/recommendations')
@login_required
def recommendations():
    return render_template('recommendations.html', recommendations=_smart_recommendations(session['user_id']))

@app.route('/recommendations/<int:listing_id>/feedback', methods=['POST'])
@login_required
def recommendation_feedback(listing_id):
    action=(request.form.get('action') or 'hide').strip()
    if action not in ('hide','like','not_interested'): action='hide'
    con=connect(); con.execute('INSERT OR IGNORE INTO recommendation_feedback(user_id,listing_id,action) VALUES(?,?,?)',
                                (session['user_id'],listing_id,action)); con.commit(); con.close()
    return jsonify({'ok':True})

@app.route('/recommendations/<int:listing_id>/click', methods=['POST'])
@login_required
def recommendation_click(listing_id):
    con=connect(); con.execute('INSERT INTO listing_events(listing_id,user_id,event_type,metadata) VALUES(?,?,?,?)',
                                (listing_id,session['user_id'],'recommendation_click','{}')); con.commit(); con.close()
    return jsonify({'ok':True})

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
