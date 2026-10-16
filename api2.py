import os, io, zipfile, subprocess, sys, datetime as dt
from flask import Blueprint, jsonify, request, send_file
from db import tx
bp = Blueprint("api2", __name__)
ROOT = os.path.dirname(os.path.abspath(__file__))
def need(d, *k):
    for x in k:
        if d.get(x) in (None, ""): raise ValueError(f"Missing field: {x}")
def table(sql, key, req):
    @bp.get(sql, endpoint=key)
    def _():
        with tx(req) as q: return jsonify(q(TABLES[key]))
TABLES = {"bills": "SELECT b.*, v.paid, v.outstanding FROM bill b JOIN v_payment_summary v USING(bill_id) ORDER BY 1",
 "payments": "SELECT * FROM payment ORDER BY payment_id DESC", "memberships": "SELECT m.*, c.full_name FROM membership m JOIN customer c USING(customer_id) ORDER BY 1",
 "packages": "SELECT p.*, (SELECT string_agg(s.service_name||' x'||ps.sessions_included, ', ') FROM package_service ps JOIN service s USING(service_id) WHERE ps.package_id=p.package_id) AS services FROM package p ORDER BY 1",
 "purchases": "SELECT * FROM v_package_session_balance ORDER BY 1", "products": "SELECT * FROM product ORDER BY product_id"}
for k, u in (("bills","/api/bills"),("payments","/api/payments"),("memberships","/api/memberships"),("packages","/api/packages"),("purchases","/api/package-purchases"),("products","/api/products")): table(u, k, "GET "+u)

@bp.put("/api/appointments/<int:aid>")
def reschedule(aid):
    d = request.get_json(force=True); need(d, "start"); start = dt.datetime.fromisoformat(d["start"])
    with tx("PUT /api/appointments") as q:
        m = q("SELECT COALESCE(SUM(duration_minutes),0) m FROM appointment_service WHERE appointment_id=%s", (aid,))[0]["m"]
        r = q("UPDATE appointment SET appointment_datetime=%s,end_datetime=%s WHERE appointment_id=%s AND status='booked' RETURNING *",
              (start, start + dt.timedelta(minutes=m), aid))
        if not r: return jsonify(error="Only existing booked appointments can be rescheduled"), 404
        return jsonify(r[0])

@bp.post("/api/bills")
def make_bill():
    d = request.get_json(force=True); need(d, "appointment_id")
    with tx("POST /api/bills") as q:
        a = q("SELECT customer_id,status FROM appointment WHERE appointment_id=%s", (d["appointment_id"],))
        if not a or a[0]["status"] == "cancelled": raise ValueError("Appointment not found or cancelled")
        sub = q("SELECT COALESCE(SUM(agreed_price),0) s FROM appointment_service WHERE appointment_id=%s", (d["appointment_id"],))[0]["s"]
        pct = q("SELECT COALESCE(MAX(discount_percent),0) p FROM membership WHERE customer_id=%s AND status='active' AND current_date BETWEEN start_date AND end_date", (a[0]["customer_id"],))[0]["p"]
        disc = round(sub * pct / 100, 2); tax = round((sub - disc) * 18 / 100, 2)
        return jsonify(q("INSERT INTO bill(appointment_id,subtotal,discount_amount,tax_amount,total_amount) VALUES(%s,%s,%s,%s,%s) RETURNING *",
                         (d["appointment_id"], sub, disc, tax, sub - disc + tax))[0]), 201

@bp.post("/api/payments")
def pay():
    d = request.get_json(force=True); need(d, "bill_id", "amount", "payment_method")
    with tx("POST /api/payments") as q:   # simulated payment: records a row only, no gateway
        return jsonify(q("INSERT INTO payment(bill_id,amount,payment_method,transaction_reference) VALUES(%s,%s,%s,%s) RETURNING *",
                         (d["bill_id"], d["amount"], d["payment_method"], "SIM-" + dt.datetime.now().strftime("%Y%m%d%H%M%S")))[0]), 201

@bp.post("/api/products")
def add_product():
    d = request.get_json(force=True); need(d, "product_name", "unit_price", "stock_quantity")
    with tx("POST /api/products") as q:
        return jsonify(q("INSERT INTO product(product_name,category,unit_price,stock_quantity,reorder_level) VALUES(%s,%s,%s,%s,%s) RETURNING *",
                         (d["product_name"], d.get("category"), d["unit_price"], d["stock_quantity"], d.get("reorder_level", 5)))[0]), 201
@bp.put("/api/products/<int:pid>")
def upd_product(pid):
    d = request.get_json(force=True); need(d, "stock_quantity")
    with tx("PUT /api/products") as q:
        r = q("UPDATE product SET stock_quantity=%s,unit_price=COALESCE(%s,unit_price) WHERE product_id=%s RETURNING *", (d["stock_quantity"], d.get("unit_price"), pid))
        return (jsonify(r[0]) if r else (jsonify(error="Not found"), 404))
@bp.post("/api/product-usage")
def usage():
    d = request.get_json(force=True); need(d, "appointment_id", "product_id", "quantity_used")
    with tx("POST /api/product-usage") as q:
        return jsonify(q("INSERT INTO product_usage(appointment_id,product_id,quantity_used) VALUES(%s,%s,%s) RETURNING *", (d["appointment_id"], d["product_id"], d["quantity_used"]))[0]), 201

@bp.post("/api/packages")
def add_package():
    d = request.get_json(force=True); need(d, "package_name", "package_price", "validity_days", "services")
    with tx("POST /api/packages") as q:
        p = q("INSERT INTO package(package_name,description,package_price,validity_days) VALUES(%s,%s,%s,%s) RETURNING *", (d["package_name"], d.get("description"), d["package_price"], d["validity_days"]))[0]
        for s in d["services"]: q("INSERT INTO package_service VALUES(%s,%s,%s)", (p["package_id"], s["service_id"], s["sessions_included"]))
        return jsonify(p), 201
@bp.post("/api/memberships")
def add_membership():
    d = request.get_json(force=True); need(d, "customer_id", "membership_name", "discount_percent", "start_date", "end_date")
    with tx("POST /api/memberships") as q:
        return jsonify(q("INSERT INTO membership(customer_id,membership_name,start_date,end_date,discount_percent) VALUES(%s,%s,%s,%s,%s) RETURNING *",
                         (d["customer_id"], d["membership_name"], d["start_date"], d["end_date"], d["discount_percent"]))[0]), 201
@bp.post("/api/package-purchases")
def buy():
    d = request.get_json(force=True); need(d, "customer_id", "package_id")
    with tx("POST /api/package-purchases") as q:
        p = q("SELECT validity_days,(SELECT SUM(sessions_included) FROM package_service WHERE package_id=%s) n FROM package WHERE package_id=%s AND active", (d["package_id"], d["package_id"]))
        if not p or not p[0]["n"]: raise ValueError("Package not found or has no services")
        return jsonify(q("INSERT INTO package_purchase(customer_id,package_id,expiry_date,sessions_remaining) VALUES(%s,%s,current_date+%s,%s) RETURNING *",
                         (d["customer_id"], d["package_id"], p[0]["validity_days"], p[0]["n"]))[0]), 201
@bp.post("/api/package-redemptions")
def redeem():
    d = request.get_json(force=True); need(d, "purchase_id", "appointment_id", "sessions_used")
    with tx("POST /api/package-redemptions") as q:
        return jsonify(q("INSERT INTO package_redemption(purchase_id,appointment_id,sessions_used) VALUES(%s,%s,%s) RETURNING *", (d["purchase_id"], d["appointment_id"], d["sessions_used"]))[0]), 201

REPORTS = {
 "today": "SELECT * FROM appointment_today_dummy",
 "upcoming": "SELECT * FROM v_upcoming_appointments ORDER BY appointment_datetime",
 "staff_utilization": "SELECT * FROM v_staff_utilization ORDER BY booked_minutes DESC",
 "service_popularity": "SELECT * FROM v_service_popularity ORDER BY bookings DESC",
 "daily_revenue": "SELECT * FROM v_daily_revenue ORDER BY day DESC",
 "revenue_by_category": "SELECT c.category_name,SUM(x.agreed_price) revenue FROM appointment_service x JOIN service s USING(service_id) JOIN service_category c USING(category_id) JOIN appointment a USING(appointment_id) WHERE a.status='completed' GROUP BY 1 ORDER BY 2 DESC",
 "visit_frequency": "SELECT * FROM v_customer_visit_history WHERE visits>0 ORDER BY visits DESC",
 "cancellations": "SELECT c.*,a.customer_id FROM appointment_cancellation c JOIN appointment a USING(appointment_id)",
 "low_stock": "SELECT * FROM v_product_inventory WHERE low_stock",
 "package_balance": "SELECT * FROM v_package_session_balance",
 "payments": "SELECT * FROM v_payment_summary ORDER BY bill_id",
 "busy_staff_subquery": "SELECT full_name FROM staff WHERE staff_id IN (SELECT staff_id FROM appointment GROUP BY staff_id HAVING COUNT(*)>=2)",
 "conflict_audit": "SELECT a.appointment_id,b.appointment_id other FROM appointment a JOIN appointment b ON a.staff_id=b.staff_id AND a.appointment_id<b.appointment_id AND a.status<>'cancelled' AND b.status<>'cancelled' AND tsrange(a.appointment_datetime,a.end_datetime)&&tsrange(b.appointment_datetime,b.end_datetime)"}
REPORTS["today"] = "SELECT * FROM v_upcoming_appointments WHERE appointment_datetime::date=current_date"
@bp.get("/api/reports")
def report_list(): return jsonify(list(REPORTS))
@bp.get("/api/reports/<key>")
def report(key):
    if key not in REPORTS: return jsonify(error="Unknown report"), 404
    import time; t = time.time()
    with tx("GET /api/reports/" + key) as q: rows = q(REPORTS[key])
    return jsonify(sql=REPORTS[key], rows=rows, ms=round((time.time() - t) * 1000, 2))

def _tables(q): return [r["table_name"] for r in q("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1")]
@bp.get("/api/db/tables")
def db_tables():
    with tx("GET /api/db/tables") as q: return jsonify([dict(name=t, rows=q(f'SELECT count(*) c FROM "{t}"')[0]["c"]) for t in _tables(q)])
@bp.get("/api/db/tables/<name>")
def db_table(name):
    with tx("GET /api/db/tables/" + name) as q:
        if name not in _tables(q): return jsonify(error="Unknown table"), 404
        cols = q("SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (name,))
        names = [c["column_name"] for c in cols]; sort = request.args.get("sort", names[0]); dr = "DESC" if request.args.get("dir") == "desc" else "ASC"
        if sort not in names: sort = names[0]
        page = max(int(request.args.get("page", 0)), 0)
        return jsonify(columns=cols,
          constraints=q("SELECT conname,contype,pg_get_constraintdef(oid) def FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY contype", ('"' + name + '"',)),
          indexes=q("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s", (name,)),
          rows=q(f'SELECT * FROM "{name}" t WHERE t::text ILIKE %s ORDER BY "{sort}" {dr} LIMIT 25 OFFSET %s', ("%" + request.args.get("q", "") + "%", page * 25)), page=page)
@bp.get("/api/db/objects")
def db_objects():
    with tx("GET /api/db/objects") as q:
        return jsonify(triggers=q("SELECT tgname,tgrelid::regclass::text tbl,pg_get_triggerdef(oid) def FROM pg_trigger WHERE NOT tgisinternal ORDER BY 2,1"),
          functions=q("SELECT proname FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY 1"),
          views=q("SELECT table_name FROM information_schema.views WHERE table_schema='public' ORDER BY 1"))
@bp.get("/api/schema")
def schema():
    with tx("GET /api/schema") as q:
        return jsonify(q("SELECT conrelid::regclass::text AS from_table,confrelid::regclass::text AS to_table,conname FROM pg_constraint WHERE contype='f' ORDER BY 1"))

WL = ["app.py","api2.py","db.py","setup_db.py","seed.py","make_er.py","requirements.txt",".env.example","README.md","supabase/schema.sql","static/index.html","static/app.js","static/style.css","tests/test_salon.py"]
def _wl(): return [f for f in WL if os.path.exists(os.path.join(ROOT, f))]
@bp.get("/api/source")
def src_list(): return jsonify([dict(name=f, lines=sum(1 for _ in open(os.path.join(ROOT, f), encoding="utf-8"))) for f in _wl()])
@bp.get("/api/source/<path:name>")
def src(name):
    if name not in _wl(): return jsonify(error="File not available"), 404
    return jsonify(name=name, content=open(os.path.join(ROOT, name), encoding="utf-8").read())
@bp.get("/api/source.zip")
def src_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for f in _wl(): z.write(os.path.join(ROOT, f), f)
    b.seek(0); return send_file(b, mimetype="application/zip", download_name="salon-source.zip")
@bp.post("/api/tests/run")
def run_tests():   # runs against TEST_DATABASE_URL (isolated salon_test DB), never the main DB
    r = subprocess.run([sys.executable, "tests/test_salon.py"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    return jsonify(returncode=r.returncode, output=(r.stdout + r.stderr)[-6000:])
