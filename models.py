"""مدل‌های SQLite پروژه جاکو."""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'jako.db'

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 full_name TEXT NOT NULL,
 mobile TEXT UNIQUE,
 email TEXT UNIQUE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'guest' CHECK(role IN ('guest','host','admin')),
 accepted_terms INTEGER NOT NULL DEFAULT 0,
 accepted_privacy INTEGER NOT NULL DEFAULT 0,
 is_active INTEGER NOT NULL DEFAULT 1,
 host_trial_ends_at TEXT,
 free_story_used INTEGER NOT NULL DEFAULT 0,
 free_host_trial_used INTEGER NOT NULL DEFAULT 0,
 blue_tick_active INTEGER NOT NULL DEFAULT 0,
 blue_tick_ends_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS listings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 host_id INTEGER NOT NULL,
 title TEXT NOT NULL,
 description TEXT,
 city TEXT,
 area TEXT,
 address TEXT,
 category TEXT,
 price_per_night INTEGER NOT NULL DEFAULT 0,
 max_guests INTEGER NOT NULL DEFAULT 1,
 bedrooms INTEGER NOT NULL DEFAULT 0,
 bathrooms INTEGER NOT NULL DEFAULT 0,
 amenities TEXT DEFAULT '',
 house_rules TEXT DEFAULT '',
 rating REAL NOT NULL DEFAULT 0,
 review_count INTEGER NOT NULL DEFAULT 0,
 latitude REAL,
 longitude REAL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','published','rejected','suspended')),
 rejection_reason TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(host_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS images (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 filename TEXT NOT NULL,
 sort_order INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bookings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 listing_id INTEGER NOT NULL,
 check_in TEXT,
 check_out TEXT,
 guests_count INTEGER NOT NULL DEFAULT 1,
 total_price INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','rejected','cancelled','completed')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(listing_id) REFERENCES listings(id)
);
CREATE TABLE IF NOT EXISTS favorites (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 listing_id INTEGER NOT NULL,
 UNIQUE(user_id, listing_id),
 FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 listing_id INTEGER NOT NULL,
 rating INTEGER NOT NULL,
 comment TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS subscription_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 duration_days INTEGER NOT NULL,
 price INTEGER NOT NULL,
 description TEXT DEFAULT '',
 is_active INTEGER NOT NULL DEFAULT 1,
 is_trial INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS blue_tick_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 duration_days INTEGER NOT NULL,
 price INTEGER NOT NULL DEFAULT 0,
 description TEXT DEFAULT '',
 is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS blue_tick_subscriptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 plan_id INTEGER NOT NULL,
 starts_at TEXT NOT NULL,
 ends_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 transaction_ref TEXT,
 UNIQUE(transaction_ref),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(plan_id) REFERENCES blue_tick_plans(id)
);
CREATE TABLE IF NOT EXISTS subscriptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 plan_id INTEGER NOT NULL,
 starts_at TEXT NOT NULL,
 ends_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active',
 transaction_ref TEXT,
 FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(plan_id) REFERENCES subscription_plans(id)
);
CREATE TABLE IF NOT EXISTS ads (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 host_id INTEGER NOT NULL,
 placement TEXT NOT NULL DEFAULT 'featured',
 starts_at TEXT,
 ends_at TEXT,
 price INTEGER NOT NULL DEFAULT 0,
 duration_hours INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','rejected','expired')),
 clicks INTEGER NOT NULL DEFAULT 0,
 impressions INTEGER NOT NULL DEFAULT 0,
 title TEXT DEFAULT '',
 image_filename TEXT DEFAULT '',
 payment_ref TEXT,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE,
 FOREIGN KEY(host_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS stories (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 host_id INTEGER NOT NULL,
 listing_id INTEGER,
 image_filename TEXT,
 caption TEXT DEFAULT '',
 duration_hours INTEGER NOT NULL DEFAULT 6,
 starts_at TEXT,
 ends_at TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','rejected','expired')),
 views INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(host_id) REFERENCES users(id),
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS listing_promotions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 host_id INTEGER NOT NULL,
 starts_at TEXT NOT NULL,
 ends_at TEXT NOT NULL,
 price INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'active',
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE,
 FOREIGN KEY(host_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 sender_id INTEGER NOT NULL,
 receiver_id INTEGER NOT NULL,
 listing_id INTEGER,
 body TEXT NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(receiver_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 title TEXT NOT NULL,
 body TEXT,
 is_read INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS coupons (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT UNIQUE NOT NULL,
 percent INTEGER NOT NULL DEFAULT 0,
 max_uses INTEGER NOT NULL DEFAULT 0,
 used_count INTEGER NOT NULL DEFAULT 0,
 expires_at TEXT,
 is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS home_slides (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 title TEXT NOT NULL,
 subtitle TEXT DEFAULT '',
 city_label TEXT DEFAULT 'خوزستان',
 image_filename TEXT,
 target_type TEXT DEFAULT 'none',
 target_listing_id INTEGER,
 external_url TEXT DEFAULT '',
 sort_order INTEGER NOT NULL DEFAULT 0,
 is_active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 kind TEXT NOT NULL,
 amount INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 ref TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 gateway TEXT DEFAULT '',
 gateway_status TEXT DEFAULT '',
 checkout_token TEXT,
 payment_url TEXT,
 metadata TEXT DEFAULT '{}',
 gateway_message TEXT DEFAULT '',
 committed_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL DEFAULT ''
);


CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 slug TEXT UNIQUE NOT NULL,
 icon_svg TEXT DEFAULT '',
 parent_id INTEGER,
 is_active INTEGER NOT NULL DEFAULT 1,
 sort_order INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(parent_id) REFERENCES categories(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 province TEXT NOT NULL DEFAULT 'خوزستان',
 name TEXT NOT NULL,
 area TEXT DEFAULT '',
 is_active INTEGER NOT NULL DEFAULT 1,
 sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ad_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 reporter_id INTEGER NOT NULL,
 reason TEXT NOT NULL,
 details TEXT DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE,
 FOREIGN KEY(reporter_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_pages (
 key TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 body TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 endpoint TEXT NOT NULL,
 subscription_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(user_id, endpoint),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS coupons_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 coupon_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 payment_id INTEGER,
 used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(coupon_id,user_id,payment_id),
 FOREIGN KEY(coupon_id) REFERENCES coupons(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payout_requests (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 amount INTEGER NOT NULL DEFAULT 0,
 iban TEXT,
 status TEXT NOT NULL DEFAULT 'pending',
 note TEXT DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 processed_at TEXT,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS otp_requests (
 mobile TEXT PRIMARY KEY,
 sent_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS support_tickets (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 subject TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT 'عمومی',
 status TEXT NOT NULL DEFAULT 'open',
 priority TEXT NOT NULL DEFAULT 'normal',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS support_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ticket_id INTEGER NOT NULL,
 sender_id INTEGER NOT NULL,
 body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE,
 FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS host_stats (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL UNIQUE,
 response_rate REAL NOT NULL DEFAULT 0,
 response_minutes INTEGER NOT NULL DEFAULT 0,
 total_views INTEGER NOT NULL DEFAULT 0,
 total_favorites INTEGER NOT NULL DEFAULT 0,
 total_bookings INTEGER NOT NULL DEFAULT 0,
 total_revenue INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS listing_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL, reason TEXT NOT NULL, details TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE, FOREIGN KEY(reporter_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS listing_availability (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'blocked',
 price INTEGER,
 UNIQUE(listing_id,date),
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS listing_status_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 status TEXT NOT NULL,
 reason TEXT DEFAULT '',
 actor_id INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE,
 FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS price_offers (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 booking_id INTEGER, user_id INTEGER NOT NULL, listing_id INTEGER NOT NULL,
 amount INTEGER NOT NULL, message TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE SET NULL,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS listing_comparisons (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, listing_id INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,listing_id),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS user_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL, reported_user_id INTEGER NOT NULL, reason TEXT NOT NULL, details TEXT DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(reporter_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(reported_user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS price_alerts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 listing_id INTEGER NOT NULL,
 last_price INTEGER NOT NULL DEFAULT 0,
 is_active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(user_id,listing_id),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS message_reactions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 message_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 reaction TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(message_id,user_id,reaction),
 FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS listing_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL,
 user_id INTEGER,
 event_type TEXT NOT NULL,
 metadata TEXT DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS recommendation_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 listing_id INTEGER NOT NULL,
 action TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(user_id,listing_id,action),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
"""

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def _add_column(con, table, column, definition):
    cols = [r['name'] for r in con.execute(f'PRAGMA table_info({table})').fetchall()]
    if column not in cols:
        con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

def init_db():
    con = connect()
    con.executescript(SCHEMA)
    for args in [
        ('users','is_active','INTEGER NOT NULL DEFAULT 1'),
        ('users','host_trial_ends_at','TEXT'),
        ('users','free_story_used','INTEGER NOT NULL DEFAULT 0'),
        ('users','free_host_trial_used','INTEGER NOT NULL DEFAULT 0'),
        ('users','blue_tick_active','INTEGER NOT NULL DEFAULT 0'),
        ('users','blue_tick_ends_at','TEXT'),
        ('users','identity_verified','INTEGER NOT NULL DEFAULT 0'),
        ('users','phone_verified','INTEGER NOT NULL DEFAULT 0'),
        ('users','profile_photo','TEXT DEFAULT ""'),
        ('users','ownership_verified','INTEGER NOT NULL DEFAULT 0'),
        ('users','theme_mode',"TEXT NOT NULL DEFAULT 'light'"),
        ('subscription_plans','is_trial','INTEGER NOT NULL DEFAULT 0'),
        ('listings','amenities',"TEXT DEFAULT ''"),
        ('listings','house_rules',"TEXT DEFAULT ''"),
        ('listings','rejection_reason','TEXT'),
        ('images','sort_order','INTEGER NOT NULL DEFAULT 0'),
        ('ads','duration_hours','INTEGER NOT NULL DEFAULT 1'),
        ('ads','title',"TEXT DEFAULT ''"),
        ('ads','image_filename',"TEXT DEFAULT ''"),
        ('ads','payment_ref',"TEXT"),
        ('bookings','notes',"TEXT DEFAULT ''"),
        ('bookings','coupon_code',"TEXT DEFAULT ''"),
        ('bookings','discount_amount','INTEGER NOT NULL DEFAULT 0'),
        ('messages','image_filename',"TEXT DEFAULT ''"),
        ('listings','weekend_price','INTEGER NOT NULL DEFAULT 0'),
        ('listings','check_in_time',"TEXT DEFAULT '14:00'"),
        ('listings','check_out_time',"TEXT DEFAULT '12:00'"),
        ('reviews','host_reply',"TEXT DEFAULT ''"),
        ('reviews','cleanliness','INTEGER'),
        ('reviews','location_rating','INTEGER'),
        ('reviews','amenities_rating','INTEGER'),
        ('reviews','host_behavior_rating','INTEGER'),
        ('reviews','value_rating','INTEGER'),
        ('reviews','is_verified',"INTEGER NOT NULL DEFAULT 0"),
        ('payments','gateway',"TEXT DEFAULT ''"),
        ('payments','gateway_status',"TEXT DEFAULT ''"),
        ('payments','checkout_token',"TEXT"),
        ('payments','payment_url',"TEXT"),
        ('payments','metadata',"TEXT DEFAULT '{}'"),
        ('payments','gateway_message',"TEXT DEFAULT ''"),
        ('payments','committed_at',"TEXT"),
        ('ads','payment_ref',"TEXT"),
        ('ads','title',"TEXT DEFAULT ''"),
        ('ads','image_filename',"TEXT DEFAULT ''"),
        ('listings','latitude','REAL'),
        ('listings','longitude','REAL'),
        ('listings','details_json',"TEXT DEFAULT '{}'"),
        ('listings','deposit','INTEGER NOT NULL DEFAULT 0'),
        ('listings','price_per_hour','INTEGER NOT NULL DEFAULT 0'),
        ('listings','price_per_week','INTEGER NOT NULL DEFAULT 0'),
        ('listings','rent_unit',"TEXT DEFAULT 'day'"),
        ('listings','is_verified', 'INTEGER NOT NULL DEFAULT 0'),
        ('listings','successful_rentals','INTEGER NOT NULL DEFAULT 0'),
        ('listings','workflow_status',"TEXT NOT NULL DEFAULT 'pending_review'"),
        ('listings','view_count','INTEGER NOT NULL DEFAULT 0'),
        ('listings','contact_clicks','INTEGER NOT NULL DEFAULT 0'),
        ('listings','expires_at','TEXT'),
        ('listings','contact_phone',"TEXT DEFAULT ''"),
        ('listings','show_contact_phone','INTEGER NOT NULL DEFAULT 1'),
        ('listings','instant_book','INTEGER NOT NULL DEFAULT 0'),
        ('listings','pets_allowed','INTEGER NOT NULL DEFAULT 0'),
        ('listings','party_allowed','INTEGER NOT NULL DEFAULT 0'),
        ('listings','parking','INTEGER NOT NULL DEFAULT 0'),
        ('listings','pool','INTEGER NOT NULL DEFAULT 0'),
        ('listings','featured','INTEGER NOT NULL DEFAULT 0'),
        ('listings','risk_score','INTEGER NOT NULL DEFAULT 0'),
        ('listings','seo_title','TEXT DEFAULT ""'),
        ('listings','seo_description','TEXT DEFAULT ""'),
        ('listings','last_booked_at','TEXT'),
        ('bookings','cancel_reason','TEXT DEFAULT ""'),
        ('bookings','deposit_amount','INTEGER NOT NULL DEFAULT 0'),
        ('reviews','reply_at','TEXT'),
        ('favorites','saved_price','INTEGER NOT NULL DEFAULT 0'),
        ('favorites','price_alert','INTEGER NOT NULL DEFAULT 1'),
        ('home_slides','target_type',"TEXT DEFAULT 'none'"),
        ('home_slides','target_listing_id','INTEGER'),
        ('home_slides','external_url',"TEXT DEFAULT ''"),
        ('messages','image_filename',"TEXT DEFAULT ''"),
        ('messages','reply_to_id','INTEGER'),
        ('messages','message_type',"TEXT NOT NULL DEFAULT 'text'"),
        ('messages','delivered_at','TEXT'),
        ('messages','read_at','TEXT'),

    ]:
        _add_column(con, *args)
    blue_plans = [
        ('تیک آبی ۳۰ روزه', 30, 150000, 'تیک آبی اشتراکی؛ قیمت و مدت از پنل مدیریت قابل تغییر است.'),
    ]
    for name, days, price, desc in blue_plans:
        if not con.execute('SELECT 1 FROM blue_tick_plans WHERE name=?', (name,)).fetchone():
            con.execute('INSERT INTO blue_tick_plans(name,duration_days,price,description,is_active) VALUES(?,?,?,?,1)', (name,days,price,desc))
    plans = [
        ('آزمایشی رایگان میزبان', 7, 0, '۷ روز رایگان ویژه ورود میزبان؛ فقط یک بار برای هر میزبان'),
        ('پایه', 7, 100000, 'مناسب شروع میزبانی'),
        ('حرفه‌ای', 30, 300000, 'برای میزبان‌های فعال'),
        ('تجاری', 90, 750000, 'برای چند اقامتگاه و تبلیغات بیشتر'),
    ]
    slides = [
        ('هر سفر، یک خاطره ماندگار', 'اقامتگاه‌های متنوع در کل خوزستان', 'خوزستان', None, 1),
        ('خونه‌ای برای هر مقصد', 'از دزفول تا اهواز، شوش و آبادان', 'خوزستان', None, 2),
        ('سفر بعدی‌ات را پیدا کن', 'اقامتگاه‌های مطمئن، جستجوی آسان و رزرو سریع', 'خوزستان', None, 3),
    ]
    for title, subtitle, city_label, image_filename, sort_order in slides:
        if not con.execute('SELECT 1 FROM home_slides WHERE sort_order=?', (sort_order,)).fetchone():
            con.execute('INSERT INTO home_slides(title,subtitle,city_label,image_filename,sort_order,is_active) VALUES(?,?,?,?,?,1)',
                        (title, subtitle, city_label, image_filename, sort_order))
    for name, days, price, desc in plans:
        if not con.execute('SELECT 1 FROM subscription_plans WHERE name=?', (name,)).fetchone():
            con.execute('INSERT INTO subscription_plans(name,duration_days,price,description,is_trial) VALUES(?,?,?,?,?)', (name,days,price,desc,1 if name=='آزمایشی رایگان میزبان' else 0))
    con.execute("UPDATE subscription_plans SET is_trial=1,duration_days=7,price=0 WHERE name='آزمایشی رایگان میزبان'")
    # تنظیمات اولیه اپ/PWA و بنر صفحه اصلی؛ همه این موارد از پنل مدیریت قابل تغییر هستند.
    default_app_settings = [
        ('app_name','جاکو'),
        ('app_short_name','جاکو'),
        ('app_theme_color','#087d50'),
        ('app_icon','images/placeholder.svg'),
        ('home_banner_enabled','1'),
        ('home_banner_title','اقامتگاهت را بیشتر دیده شو!'),
        ('home_banner_text','تبلیغ قابل کلیک بساز و کاربران را مستقیم به صفحه اقامتگاهت هدایت کن.'),
        ('home_banner_button','شروع تبلیغ'),
        ('home_banner_target_type','ads'),
        ('home_banner_listing_id',''),
        ('home_banner_external_url',''),
        ('home_banner_image',''),
        ('push_vapid_public_key',''),
    ]
    for key,value in default_app_settings:
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES(?,?)",(key,value))

    con.execute("DELETE FROM app_settings WHERE key='google_maps_api_key'")
    
    seed_categories = [
        ('اقامتگاه','stay','<svg viewBox="0 0 24 24"><path d="M3 10.5 12 3l9 7.5V21H3z"/><path d="M9 21v-6h6v6"/></svg>',None,1,1),
        ('اجاره ماشین','car','<svg viewBox="0 0 24 24"><path d="M5 16h14l-1.5-6h-11z"/><circle cx="7" cy="17" r="1.5"/><circle cx="17" cy="17" r="1.5"/></svg>',None,1,2),
        ('تجهیزات و لوازم','equipment','<svg viewBox="0 0 24 24"><path d="M4 7h16v13H4z"/><path d="M8 7V4h8v3M8 11h8"/></svg>',None,1,3),
        ('لوازم گیمینگ','gaming','<svg viewBox="0 0 24 24"><path d="M7 9h10l3 7-2 2-4-3H10l-4 3-2-2z"/><path d="M8 12h3M9.5 10.5v3"/><circle cx="16.5" cy="11.5" r=".8"/><circle cx="18" cy="13" r=".8"/></svg>',None,1,4),
        ('دوربین و تصویربرداری','camera','<svg viewBox="0 0 24 24"><path d="M4 7h4l1.5-2h5L16 7h4v12H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',None,1,5),
        ('جشن و مراسم','events','<svg viewBox="0 0 24 24"><path d="M5 18 8 6l8 3-4 12z"/><path d="M16 4v4M20 6v4M18 4v2"/></svg>',None,1,6),
        ('کمپ و سفر','camp','<svg viewBox="0 0 24 24"><path d="m4 20 8-15 8 15H4z"/><path d="M12 5v15"/></svg>',None,1,7),
        ('ورزش و تفریح','sport','<svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><path d="m9 9 3 2 3-2M12 11v5M8 21l4-5 4 5"/></svg>',None,1,8),
    ]
    for c in seed_categories:
        con.execute('INSERT OR IGNORE INTO categories(name,slug,icon_svg,parent_id,is_active,sort_order) VALUES(?,?,?,?,?,?)',c)
    subcats = {
        'stay':[('ویلا','villa'),('آپارتمان','apartment'),('سوئیت','suite'),('بومگردی','ecotourism'),('کلبه','cabin')],
        'car':[('سواری','car-passenger'),('شاسی‌بلند','car-suv'),('لوکس','car-luxury'),('با راننده','car-driver')],
        'equipment':[('ابزار','tools'),('لوازم خانه','home-equipment'),('تجهیزات کاری','work-equipment')],
        'gaming':[('PS5','ps5'),('Xbox','xbox'),('PC Gaming','pc-gaming'),('Nintendo','nintendo')],
        'camera':[('دوربین','camera-body'),('لنز','camera-lens'),('تجهیزات نور','camera-light')],
        'events':[('میز و صندلی','event-furniture'),('صوت و نور','event-av'),('دکور','event-decor')],
        'camp':[('چادر','tent'),('کیسه خواب','sleeping-bag'),('تجهیزات کمپ','camp-gear')],
        'sport':[('دوچرخه','bicycle'),('ورزش آبی','water-sport'),('ورزش و بازی','sport-games')]
    }
    for parent_slug, children in subcats.items():
        parent=con.execute('SELECT id FROM categories WHERE slug=?',(parent_slug,)).fetchone()
        if parent:
            for i,(name,slug) in enumerate(children,1):
                con.execute('INSERT OR IGNORE INTO categories(name,slug,parent_id,is_active,sort_order) VALUES(?,?,?,?,?)',(name,slug,parent['id'],1,i))

        con.execute('UPDATE categories SET name=?, icon_svg=?, parent_id=?, is_active=?, sort_order=? WHERE slug=?',
                    (c[0],c[2],c[3],c[4],c[5],c[1]))
    cities = ['اهواز','دزفول','شوش','شوشتر','اندیمشک','آبادان','خرمشهر','مسجدسلیمان','ایذه','بهبهان','ماهشهر','رامهرمز','شادگان','سوسنگرد','هفتکل']
    for i,city in enumerate(cities,1):
        con.execute('INSERT OR IGNORE INTO cities(province,name,sort_order) VALUES(?,?,?)',('خوزستان',city,i))
    con.execute("INSERT OR IGNORE INTO content_pages(key,title,body) VALUES('terms','قوانین و مقررات اپلیکیشن جاکو','')")
    con.execute("INSERT OR IGNORE INTO content_pages(key,title,body) VALUES('privacy','حریم خصوصی اپلیکیشن جاکو','')")
    # همگام‌سازی وضعیت گردش‌کار نسخه‌های قدیمی با سیستم جدید بررسی آگهی.
    con.execute("UPDATE listings SET workflow_status=CASE status WHEN 'published' THEN 'approved' WHEN 'rejected' THEN 'rejected' WHEN 'suspended' THEN 'needs_revision' ELSE 'pending_review' END WHERE workflow_status IS NULL OR workflow_status='' OR workflow_status='pending_review'")
    con.execute("INSERT OR IGNORE INTO listing_status_history(listing_id,status,reason) SELECT id,workflow_status,COALESCE(rejection_reason,'') FROM listings WHERE id NOT IN (SELECT listing_id FROM listing_status_history)")
    con.commit(); con.close()
