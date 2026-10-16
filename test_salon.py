import os, sys, unittest, datetime as dt
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://salon:salon@127.0.0.1:5432/salon_test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2, setup_db, seed
from db import tx
import app as A
class T(unittest.TestCase):
    @classmethod
    def setUpClass(c):
        with psycopg2.connect(os.environ["DATABASE_URL"]) as cn, cn.cursor() as cur:   # isolated test DB only
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        setup_db.main()
        seed.main(); c.c = A.app.test_client()
    def slot(s, staff=1, svc=1, day=3, hour=10):
        return (dt.date.today()+dt.timedelta(days=day)).isoformat()+f"T{hour:02d}:00"
    def book(s, **k):
        d = dict(customer_id=1, staff_id=1, start=s.slot(), service_ids=[1]); d.update(k)
        return s.c.post("/api/appointments", json=d)
    def test_01_health(s): s.assertEqual(s.c.get("/api/health").json["database"], "connected")
    def test_02_customer_and_duplicate(s):
        r = s.c.post("/api/customers", json=dict(full_name="T", email="t@x.com")); s.assertEqual(r.status_code, 201)
        s.assertEqual(s.c.post("/api/customers", json=dict(full_name="T", email="t@x.com")).status_code, 409)
        s.assertEqual(s.c.post("/api/customers", json=dict(full_name="T", email="bad")).status_code, 422)
    def test_03_book_conflict_cancel_release(s):
        r = s.book(); s.assertEqual(r.status_code, 201, r.json); aid = r.json["appointment_id"]
        s.assertEqual(s.book(customer_id=2).status_code, 409)                      # overlap rejected by EXCLUDE
        s.assertEqual(s.c.post(f"/api/appointments/{aid}/cancel").status_code, 200)
        s.assertEqual(s.book(customer_id=2).status_code, 201)                      # slot released
        s.assertEqual(s.c.post(f"/api/appointments/{aid}/complete").status_code, 422)  # invalid transition
    def test_04_rules(s):
        s.assertEqual(s.book(staff_id=3, service_ids=[1], start=s.slot(day=4)).status_code, 422)   # unqualified
        s.assertEqual(s.book(start=s.slot(day=5, hour=22)).status_code, 422)                       # outside hours
        s.assertEqual(s.book(start="2020-01-01T10:00").status_code, 422)                           # past
        with tx() as q: n = q("SELECT count(*) c FROM appointment_service WHERE appointment_id NOT IN (SELECT appointment_id FROM appointment)")[0]["c"]
        s.assertEqual(n, 0)
    def test_05_sql_console(s):
        for bad in ["DELETE FROM customer", "SELECT 1; SELECT 2", "UPDATE customer SET full_name='x'"]:
            s.assertEqual(s.c.post("/api/db/query", json=dict(sql=bad)).status_code, 400)
        s.assertEqual(s.c.post("/api/db/query", json=dict(sql="SELECT count(*) FROM customer")).status_code, 200)
    def test_06_log(s): s.assertTrue(any(e["sql"] == "COMMIT" for e in s.c.get("/api/log").json))

    def test_07_reschedule(s):
        aid = s.book(customer_id=3, start=s.slot(day=6)).json["appointment_id"]
        r = s.c.put(f"/api/appointments/{aid}", json=dict(start=s.slot(day=6, hour=14))); s.assertEqual(r.status_code, 200, r.json)
        s.assertEqual(s.c.put(f"/api/appointments/{aid}", json=dict(start=s.slot(day=6, hour=22))).status_code, 422)
    def test_08_bill_payment(s):
        aid = s.book(customer_id=4, start=s.slot(day=7)).json["appointment_id"]
        b = s.c.post("/api/bills", json=dict(appointment_id=aid)); s.assertEqual(b.status_code, 201, b.json)
        tot = float(b.json["total_amount"]); s.assertGreater(tot, 0)
        s.assertEqual(s.c.post("/api/payments", json=dict(bill_id=b.json["bill_id"], amount=tot + 1, payment_method="upi")).status_code, 422)
        s.assertEqual(s.c.post("/api/payments", json=dict(bill_id=b.json["bill_id"], amount=tot, payment_method="upi")).status_code, 201)
        s.assertTrue(any(x["bill_id"] == b.json["bill_id"] and x["payment_status"] == "paid" for x in s.c.get("/api/bills").json))
    def test_09_stock(s):
        aid = s.book(customer_id=6, start=s.slot(day=9)).json["appointment_id"]
        p = s.c.post("/api/products", json=dict(product_name="T", unit_price=10, stock_quantity=2)).json["product_id"]
        u = lambda n: s.c.post("/api/product-usage", json=dict(appointment_id=aid, product_id=p, quantity_used=n)).status_code
        s.assertEqual(u(3), 422); s.assertEqual(u(2), 201)
        s.assertEqual([x for x in s.c.get("/api/products").json if x["product_id"] == p][0]["stock_quantity"], 0)
    def test_10_package(s):
        pk = s.c.post("/api/packages", json=dict(package_name="TP", package_price=100, validity_days=30, services=[dict(service_id=1, sessions_included=2)]))
        s.assertEqual(pk.status_code, 201, pk.json)
        pu = s.c.post("/api/package-purchases", json=dict(customer_id=7, package_id=pk.json["package_id"])).json["purchase_id"]
        aid = s.book(customer_id=7, start=s.slot(day=10)).json["appointment_id"]
        rd = lambda n: s.c.post("/api/package-redemptions", json=dict(purchase_id=pu, appointment_id=aid, sessions_used=n)).status_code
        s.assertEqual(rd(3), 422); s.assertEqual(rd(1), 201); s.assertEqual(rd(2), 422)
    def test_11_reports(s):
        for k in s.c.get("/api/reports").json: s.assertEqual(s.c.get("/api/reports/" + k).status_code, 200, k)
    def test_12_explorer(s):
        names = {t["name"] for t in s.c.get("/api/db/tables").json}
        for t in ["customer","service_category","service","staff","staff_skill","staff_schedule","staff_break","appointment","appointment_service","package","package_service","membership","product","product_usage","bill","payment","package_purchase","package_redemption","appointment_cancellation"]: s.assertIn(t, names)
        d = s.c.get("/api/db/tables/appointment").json; s.assertTrue(d["constraints"] and d["columns"])
        s.assertEqual(s.c.get("/api/db/tables/pg_user").status_code, 404)
        s.assertEqual(s.c.get('/api/db/tables/appointment?sort=x;DROP').status_code, 200)
    def test_13_source_whitelist(s):
        for n in [".env", "../app.py", "db.py/../.env", "/etc/passwd"]: s.assertEqual(s.c.get("/api/source/" + n).status_code, 404, n)
        s.assertEqual(s.c.get("/api/source/app.py").status_code, 200)
        s.assertNotIn(".env", [f["name"] for f in s.c.get("/api/source").json])
    def test_14_rollback_and_cancel_record(s):
        n = lambda: s.c.get("/api/appointments").json.__len__()
        before = n(); s.assertEqual(s.book(staff_id=3, start=s.slot(day=11)).status_code, 422); s.assertEqual(n(), before)
        aid = s.book(customer_id=8, start=s.slot(day=11)).json["appointment_id"]
        s.c.post(f"/api/appointments/{aid}/cancel", json=dict(reason="because"))
        with tx() as q: s.assertEqual(q("SELECT reason FROM appointment_cancellation WHERE appointment_id=%s", (aid,))[0]["reason"], "because")

    def test_15_crud(s):
        r = s.c.post("/api/customers", json=dict(full_name="Del", email="del@x.com")).json["customer_id"]
        s.assertEqual(s.c.put(f"/api/customers/{r}", json=dict(full_name="Del2", email="del@x.com")).json["full_name"], "Del2")
        s.assertEqual(s.c.delete(f"/api/customers/{r}").status_code, 200); s.assertEqual(s.c.get(f"/api/customers/{r}").status_code, 404)
        s.assertEqual(s.c.delete("/api/customers/1").status_code, 409)             # referenced by appointments
        s.assertEqual(s.c.post("/api/services", json=dict(category_id=1, service_name="X", duration_minutes=30, base_price=-5)).status_code, 422)
        sv = s.c.post("/api/services", json=dict(category_id=1, service_name="Tmp", duration_minutes=30, base_price=100)); s.assertEqual(sv.status_code, 201)
        s.assertEqual(s.c.put(f"/api/services/{sv.json['service_id']}", json=dict(base_price=150)).json["base_price"], "150.00")
        st = s.c.post("/api/staff", json=dict(full_name="Tmp Staff", role="Stylist")).json["staff_id"]
        s.assertEqual(s.c.post(f"/api/staff/{st}/skills", json=dict(service_id=sv.json["service_id"], skill_level=4)).status_code, 201)
        day = (dt.date.today() + dt.timedelta(days=20)).isoformat()
        s.assertEqual(s.c.post(f"/api/staff/{st}/schedule", json=dict(working_date=day, start_time="09:00", end_time="12:00")).status_code, 201)
        s.assertEqual(s.c.post(f"/api/staff/{st}/schedule", json=dict(working_date=day, start_time="12:00", end_time="09:00")).status_code, 422)
        s.assertEqual(len(s.c.get(f"/api/staff/{st}/schedule").json), 1)
    def test_16_lab_dashboard(s):
        for q in s.c.get("/api/queries").json: s.assertEqual(s.c.get("/api/queries/" + q["key"]).status_code, 200, q["key"])
        s.assertEqual(s.c.get("/api/queries/conflict_audit").json["rows"], [])
        s.assertGreater(float(s.c.get("/api/dashboard").json["revenue_collected"]), 0); s.assertEqual(s.c.get("/api/meta").status_code, 200)
    def test_17_seed_integrity(s):
        with tx() as q: n = q("SELECT count(*) c FROM appointment")[0]["c"]
        s.assertGreaterEqual(n, 40)
if __name__ == "__main__": unittest.main(verbosity=2)
